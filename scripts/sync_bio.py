"""
Reconstruieste automat pagina de bio (public/index.html) cu:
  - produsele fixe din data/featured_products.json (cele din videoclipuri, editate manual)
  - produsele "hot" luate live din AliExpress Affiliate API (filtrate dupa rating/comenzi)

Ruleaza zilnic prin GitHub Actions (.github/workflows/daily-refresh.yml).
Variabile de mediu necesare (secrets in GitHub): ALI_APP_KEY, ALI_APP_SECRET, ALI_TRACKING_ID
"""
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
FEATURED_PATH = ROOT / "data" / "featured_products.json"
IMG_DIR = ROOT / "data" / "img"
OUT_DIR = ROOT / "public"

API_URL = "https://api-sg.aliexpress.com/sync"
LOG: list[str] = []


def log(msg: str) -> None:
    print(msg, file=sys.stderr)
    LOG.append(msg)

# ---- reguli de filtrare pentru produsele "hot" ----
MIN_RATING_PERCENT = 90   # evaluate_rate minim (ex. "95.2%")
MIN_ORDERS = 300          # comenzi minime
MAX_HOT_PRODUCTS = 10     # cate produse hot afisam sub cele fixe
SHIP_TO = "RO"            # doar produse livrabile in Romania
CURRENCY = "EUR"          # API-ul nu suporta RON
KEYWORDS = ["home gadget", "cleaning", "kitchen", "phone accessories",
            "car accessories", "beauty tools", "pet supplies"]  # se rotesc zilnic, pentru varietate
PER_KEYWORD = 3                                       # max produse din aceeasi categorie
VIDEO_LOG_PATH = ROOT / "data" / "video_log.json"     # produsele care au primit clip (salvat in repo)
CLIPS_PER_DAY = int(os.environ.get("CLIPS_PER_DAY", "3"))
CLIP_PRODUCTS_DAYS = 30                               # cat timp raman pe pagina produsele din clipuri
CLIP_FILES_DAYS = 7                                   # cat timp raman clipurile in consola
CLIP_CACHE = ROOT / "data" / ".cache" / "clips"       # pastrate intre rulari (cache GitHub)
SITE_URL = "https://trendixeu.netlify.app"


def sign(secret: str, params: dict) -> str:
    raw = secret + "".join(f"{k}{params[k]}" for k in sorted(params)) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def call_api(method: str, app_params: dict) -> dict:
    key = os.environ["ALI_APP_KEY"].strip()
    secret = os.environ["ALI_APP_SECRET"].strip()
    params = {
        "app_key": key,
        "format": "json",
        "method": method,
        "sign_method": "md5",
        "timestamp": str(int(time.time() * 1000)),
        "v": "2.0",
        **{k: str(v) for k, v in app_params.items() if v is not None},
    }
    params["sign"] = sign(secret, params)
    r = requests.post(API_URL, data=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error_response" in data:
        raise RuntimeError(f"API error: {data['error_response']}")
    return data


METHODS = [
    ("aliexpress.affiliate.hotproduct.query", "aliexpress_affiliate_hotproduct_query_response"),
    ("aliexpress.affiliate.product.query", "aliexpress_affiliate_product_query_response"),
]


def query_products(kw: str, tracking_id: str) -> list[dict]:
    """Incearca intai API-ul de hot products, apoi cautarea standard daca nu avem permisiune."""
    for method, resp_key in METHODS:
        try:
            data = call_api(method, {
                "keywords": kw,
                "page_size": 50,
                "sort": "LAST_VOLUME_DESC",
                "ship_to_country": SHIP_TO,
                "target_currency": CURRENCY,
                "target_language": "EN",
                "tracking_id": tracking_id,
            })
        except RuntimeError as exc:
            if "InsufficientPermission" in str(exc):
                continue
            raise
        resp = data.get(resp_key, {}).get("resp_result", {})
        if not resp:
            log(f"[warn] raspuns neasteptat ({method}): {str(data)[:300]}")
            return []
        if str(resp.get("resp_code")) != "200":
            log(f"[warn] '{kw}' ({method}): {resp.get('resp_code')} {resp.get('resp_msg')}")
            return []
        products = (resp.get("result") or {}).get("products", {}).get("product", []) or []
        log(f"[info] '{kw}' via {method.split('.')[-2]}: {len(products)} produse primite")
        return products
    log("[warn] aplicatia nu are permisiune nici la hotproduct, nici la product.query")
    return []


def fetch_hot_products() -> list[dict]:
    tracking_id = os.environ["ALI_TRACKING_ID"].strip()
    seen, picked = set(), []
    day = datetime.now(timezone.utc).toordinal()
    order = KEYWORDS[day % len(KEYWORDS):] + KEYWORDS[:day % len(KEYWORDS)]
    for kw in order:
        taken = 0
        for p in query_products(kw, tracking_id):
            if taken >= PER_KEYWORD:
                break
            pid = p.get("product_id")
            if pid in seen or not p.get("promotion_link"):
                continue
            rating = float(str(p.get("evaluate_rate", "0")).rstrip("%") or 0)
            orders = int(p.get("lastest_volume") or 0)
            if rating < MIN_RATING_PERCENT or orders < MIN_ORDERS:
                continue
            seen.add(pid)
            taken += 1
            smalls = p.get("product_small_image_urls") or []
            if isinstance(smalls, dict):
                smalls = smalls.get("string") or []
            picked.append({
                "kw": kw,
                "images": [u for u in smalls if isinstance(u, str)][:5],
                "video": p.get("product_video_url") or "",
                "id": f"p{pid}",
                "section": "hot",
                "orders": orders,
                "rating": rating,
                "title": p.get("product_title", "Produs")[:90],
                "cur": "EUR",
                "amount": float(p.get("target_sale_price") or p.get("sale_price") or 0),
                "link": p["promotion_link"],
                "image": p.get("product_main_image_url", ""),
                "meta": f"{rating:.0f}% recenzii pozitive · {orders}+ comenzi",
            })
            if len(picked) >= MAX_HOT_PRODUCTS:
                return picked
    return picked


def fetch_orders(days: int = 30) -> dict:
    """Comenzile facute prin linkurile tale (daca aplicatia are permisiune)."""
    pst = timezone(timedelta(hours=-8))  # AliExpress cere ora Pacificului
    end = datetime.now(pst)
    start = end - timedelta(days=days)
    orders, error = [], None
    for status in ("Payment Completed", "Buyer Confirmed Receipt"):
        try:
            data = call_api("aliexpress.affiliate.order.list", {
                "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
                "status": status,
                "page_no": 1,
                "page_size": 50,
            })
        except Exception as exc:
            error = "fără permisiune" if "InsufficientPermission" in str(exc) else str(exc)[:200]
            log(f"[warn] comenzi ({status}): {error}")
            continue
        resp = data.get("aliexpress_affiliate_order_list_response", {}).get("resp_result", {})
        if str(resp.get("resp_code")) == "405":
            log(f"[info] comenzi ({status}): nicio comanda in ultimele {days} zile")
            continue
        if str(resp.get("resp_code")) not in ("200", "None") and resp.get("resp_code") is not None:
            log(f"[info] comenzi ({status}): {resp.get('resp_code')} {resp.get('resp_msg')}")
            continue
        items = ((resp.get("result") or {}).get("orders") or {}).get("order", []) or []
        for o in items:
            orders.append({
                "title": str(o.get("product_title", ""))[:120],
                "product_id": o.get("product_id"),
                "paid": o.get("paid_amount") or o.get("finished_amount"),
                "commission": o.get("estimated_paid_commission") or o.get("estimated_finished_commission"),
                "status": o.get("order_status") or status,
                "time": o.get("paid_time") or o.get("created_time"),
                "count": o.get("product_count"),
            })
        log(f"[info] comenzi ({status}): {len(items)} in ultimele {days} zile")
    return {"days": days, "items": orders, "error": error if not orders else None}


LANGS = ["ro", "en", "it", "de", "fr", "es"]
CACHE_PATH = ROOT / "data" / ".cache" / "translations.json"
TRANSLATE_BUDGET = 4500  # caractere/zi (limita gratuita MyMemory e ~5000)
_tr_cache: dict = {}
_tr_used = 0
_tr_stats = {"cache": 0, "new": 0, "failed": 0, "skipped": 0}


def load_tr_cache() -> None:
    global _tr_cache
    try:
        _tr_cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _tr_cache = {}


def save_tr_cache() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(_tr_cache, ensure_ascii=False), encoding="utf-8")


def short_title(t: str) -> str:
    """Titlurile AliExpress sunt pline de cuvinte-cheie: pastram partea principala."""
    t = " ".join(str(t).split())
    head = t.split(",")[0].strip()
    if len(head) < 18:
        head = t
    if len(head) > 70:
        head = head[:70].rsplit(" ", 1)[0]
    return head


def translate(text: str, src: str, tgt: str) -> str | None:
    global _tr_used
    if src == tgt or not text:
        return text
    key = f"{src}|{tgt}|{text}"
    if key in _tr_cache:
        _tr_stats["cache"] += 1
        return _tr_cache[key]
    if _tr_used + len(text) > TRANSLATE_BUDGET:
        _tr_stats["skipped"] += 1
        return None
    try:
        r = requests.get("https://api.mymemory.translated.net/get",
                         params={"q": text, "langpair": f"{src}|{tgt}"}, timeout=20)
        _tr_used += len(text)
        data = r.json()
        out = ((data.get("responseData") or {}).get("translatedText") or "").strip()
        if str(data.get("responseStatus")) != "200" or not out or "MYMEMORY WARNING" in out.upper():
            _tr_stats["failed"] += 1
            return None
        out = html.unescape(out)
        out = out[:1].upper() + out[1:]
        _tr_cache[key] = out
        _tr_stats["new"] += 1
        return out
    except Exception:
        _tr_stats["failed"] += 1
        return None


def localize(items: list[dict], src: str) -> None:
    for p in items:
        base = short_title(p["title"]) if src == "en" else p["title"]
        titles = dict(p.get("titles") or {})  # traducerile scrise manual au prioritate
        titles.setdefault(src, base)
        for lang in LANGS:
            if lang not in titles:
                titles[lang] = translate(base, src, lang) or (titles.get("en") or base)
        p["titles"] = titles


FALLBACK_RATES = {"EUR": 1.0, "RON": 4.97}


def get_rates() -> dict:
    """Cursuri BCE, baza EUR (ex. {"RON": 4.97, "USD": 1.08, ...})."""
    try:
        r = requests.get("https://api.frankfurter.app/latest", params={"from": "EUR"}, timeout=20)
        r.raise_for_status()
        rates = {"EUR": 1.0, **r.json()["rates"]}
        log(f"[info] curs BCE: 1 EUR = {rates.get('RON')} RON")
        return rates
    except Exception as exc:
        log(f"[warn] nu am putut lua cursul valutar, folosesc rezerva: {exc}")
        return dict(FALLBACK_RATES)


def normalize_price(p: dict) -> dict:
    """Produsele fixe au pretul ca text ("RON 386.51") -> moneda + suma."""
    p.setdefault("id", str(p.get("link", "")).rstrip("/").rsplit("/", 1)[-1][:40] or p.get("title", "")[:20])
    p.setdefault("section", "featured")
    if "cur" not in p:
        cur, _, amt = str(p.get("price", "")).partition(" ")
        p["cur"] = cur or "RON"
        p["amount"] = float(amt.replace(",", ".") or 0)
    return p


def lei_text(p: dict, rates: dict) -> str:
    """Textul afisat fara JavaScript: in lei."""
    if p["cur"] == "RON":
        return f"{p['amount']:.2f}".replace(".", ",") + " lei"
    ron = p["amount"] / rates.get(p["cur"], 1) * rates.get("RON", 4.97)
    return "≈ " + f"{ron:.2f}".replace(".", ",") + " lei"


def card_html(p: dict, rates: dict) -> str:
    e = html.escape
    t = p.get("titles") or {}
    title_ro = t.get("ro") or p["title"]
    meta = ""
    if p.get("orders"):
        meta = (f'<p class="meta" data-r="{p.get("rating", 0):.0f}" data-o="{p["orders"]}">'
                f'{p.get("rating", 0):.0f}% recenzii pozitive · {p["orders"]}+ comenzi</p>')
    return f"""
  <a class="card" href="{e(p['link'])}" target="_blank" rel="noopener" data-id="{e(p.get('id', ''))}" data-s="{e(p.get('section', ''))}" data-title="{e(title_ro[:120])}" data-num="{p.get('num', '')}">
    <div class="thumbwrap"><img class="thumb" src="{e(p['image'])}" alt="" loading="lazy">{f'<span class="num">#{p["num"]}</span>' if p.get("num") else ""}</div>
    <div class="info">
      <p class="title" data-tid="{e(p.get('id', ''))}">{e(title_ro)}</p>
      {meta}
      <div class="row"><span class="price" data-cur="{p['cur']}" data-amount="{p['amount']}">{lei_text(p, rates)}</span><span class="buy" data-i18n="buy">Cumpără</span></div>
    </div>
  </a>"""


def render(featured: list[dict], hot: list[dict], rates: dict, clip_items: list[dict] | None = None) -> str:
    clip_items = clip_items or []
    titles_json = json.dumps({p.get("id"): p.get("titles", {}) for p in clip_items + featured + hot}, ensure_ascii=False).replace("</", "<\\/")
    clips_section = ""
    if clip_items:
        clips_section = f"""
  <h2 class="section-title" data-i18n="clips">Din clipurile TikTok</h2>
  {''.join(card_html(p, rates) for p in clip_items)}"""
    now = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    hot_section = ""
    if hot:
        hot_section = f"""
  <h2 class="section-title" data-i18n="hot" data-date="{datetime.now(timezone.utc).date().isoformat()}">Oferte noi · actualizat {now}</h2>
  {''.join(card_html(p, rates) for p in hot)}"""
    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>@trendixeu — Oferte zilnice / Daily deals</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<script src="/track.js" defer></script>
<script src="/i18n.js" defer></script>
<style>
:root{{--bg:#121016;--bg2:#221a2c;--card:#1e1c26;--text:#f5f3f0;--muted:#a9a5b3;--hot:#ff5a3c;--gold:#ffd166;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:linear-gradient(180deg,var(--bg),var(--bg2));min-height:100vh;color:var(--text);font-family:Inter,sans-serif;padding:28px 16px 60px;}}
.wrap{{max-width:480px;margin:0 auto;}}
.avatar{{width:84px;height:84px;border-radius:50%;margin:0 auto;background:linear-gradient(135deg,var(--hot),var(--gold));display:flex;align-items:center;justify-content:center;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:30px;color:#121016;}}
h1{{font-family:'Space Grotesk',sans-serif;font-size:22px;text-align:center;margin:12px 0 4px;}}
.tag{{text-align:center;color:var(--muted);font-size:14px;margin-bottom:22px;}}
.section-title{{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:28px 0 12px;}}
.card{{display:flex;gap:14px;background:var(--card);border-radius:16px;padding:12px;margin-bottom:12px;text-decoration:none;color:var(--text);align-items:center;}}
.thumbwrap{{position:relative;flex-shrink:0;}}
.thumb{{width:96px;height:96px;object-fit:cover;border-radius:12px;display:block;background:#333;}}
.num{{position:absolute;top:-8px;left:-8px;min-width:36px;height:36px;padding:0 8px;border-radius:999px;background:var(--hot);color:#fff;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:16px;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,.4);}}
.find{{display:flex;gap:8px;margin:0 0 18px;}}
.find input{{flex:1;min-width:0;background:var(--card);border:1px solid #3a3446;border-radius:12px;padding:10px 12px;color:var(--text);font:inherit;font-size:15px;}}
.find button{{background:var(--hot);color:#fff;border:0;border-radius:12px;padding:0 16px;font:inherit;font-weight:600;}}
.card.flash{{outline:3px solid var(--gold);}}
.info{{flex:1;min-width:0;}}
.title{{font-size:14px;line-height:1.3;margin-bottom:6px;}}
.meta{{font-size:12px;color:var(--muted);margin-bottom:6px;}}
.row{{display:flex;justify-content:space-between;align-items:center;gap:8px;}}
.price{{color:var(--gold);font-weight:700;font-size:15px;}}
.buy{{background:var(--hot);color:#fff;border-radius:999px;padding:6px 14px;font-size:13px;font-weight:600;white-space:nowrap;}}
</style>
</head>
<body>
<div class="wrap">
  <div class="avatar">TX</div>
  <h1>@trendixeu</h1>
  <p class="tag" data-i18n="tag">Oferte zilnice</p>
  <form class="find" id="find"><input id="findNum" inputmode="numeric" placeholder="Ai văzut un număr în clip? Ex: 12" data-i18n-ph="find" aria-label="Numărul produsului"><button type="submit" data-i18n="go">Caută</button></form>
  {clips_section}
  <h2 class="section-title" data-i18n="featured">Din clipurile mele</h2>
  {''.join(card_html(p, rates) for p in featured)}
  {hot_section}
</div>
<script>
document.getElementById("find").addEventListener("submit", function (ev) {{
  ev.preventDefault();
  var n = String(document.getElementById("findNum").value).replace(/[^0-9]/g, "");
  var el = n && document.querySelector('.card[data-num="' + n + '"]');
  if (!el) {{ document.getElementById("findNum").value = ""; return; }}
  el.scrollIntoView({{ behavior: "smooth", block: "center" }});
  el.classList.add("flash"); setTimeout(function () {{ el.classList.remove("flash"); }}, 2200);
}});
if (/^#\d+$/.test(location.hash)) {{
  var el0 = document.querySelector('.card[data-num="' + location.hash.slice(1) + '"]');
  if (el0) setTimeout(function () {{ el0.scrollIntoView({{ block: "center" }}); el0.classList.add("flash"); }}, 300);
}}
</script>
<script>
window.TX_TITLES = {titles_json};
</script>
<script>
window.RATES = {json.dumps(rates)};
(function () {{
  var TZ = {{
    "Europe/Bucharest": "RON", "Europe/London": "GBP", "Europe/Dublin": "EUR",
    "Europe/Warsaw": "PLN", "Europe/Budapest": "HUF", "Europe/Prague": "CZK",
    "Europe/Zurich": "CHF", "Europe/Stockholm": "SEK", "Europe/Copenhagen": "DKK",
    "Europe/Oslo": "NOK", "Europe/Istanbul": "TRY", "Asia/Tokyo": "JPY",
    "America/Toronto": "CAD", "America/Vancouver": "CAD", "America/Montreal": "CAD"
  }};
  var REGION = {{
    RO: "RON", GB: "GBP", US: "USD", PL: "PLN", HU: "HUF", CZ: "CZK", CH: "CHF",
    SE: "SEK", DK: "DKK", NO: "NOK", TR: "TRY", CA: "CAD", AU: "AUD", JP: "JPY",
    IN: "INR", BR: "BRL", MX: "MXN", NZ: "NZD", IL: "ILS", KR: "KRW", ZA: "ZAR"
  }};
  function fromTimezone() {{
    try {{
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
      if (TZ[tz]) return TZ[tz];
      if (tz.indexOf("Australia/") === 0) return "AUD";
      if (tz.indexOf("America/") === 0 && tz.indexOf("America/Argentina") !== 0 && tz.indexOf("America/Sao_Paulo") !== 0 && tz.indexOf("America/Mexico") !== 0) return "USD";
      if (tz.indexOf("Europe/") === 0) return "EUR";
    }} catch (e) {{}}
    return null;
  }}
  function fromLanguage() {{
    var langs = navigator.languages || [navigator.language || ""];
    for (var i = 0; i < langs.length; i++) {{
      var l = String(langs[i]);
      var m = l.match(/[-_]([A-Za-z]{{2}})$/);
      if (m && REGION[m[1].toUpperCase()]) return REGION[m[1].toUpperCase()];
      if (l.toLowerCase().indexOf("ro") === 0) return "RON";
    }}
    return null;
  }}
  var cur = fromTimezone() || fromLanguage() || "EUR";
  var R = window.RATES || {{}};
  if (!R[cur]) cur = "EUR";
  var locale = (navigator.languages && navigator.languages[0]) || navigator.language || "ro-RO";
  if (/^(ar|he|fa|ur)/i.test(locale)) locale = "en-GB";
  var fmt;
  if (cur === "RON") {{
    var nf = new Intl.NumberFormat("ro-RO", {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    fmt = {{ format: function (v) {{ return nf.format(v) + " lei"; }} }};
  }} else {{
    try {{ fmt = new Intl.NumberFormat(locale, {{ style: "currency", currency: cur, currencyDisplay: "narrowSymbol" }}); }}
    catch (e) {{
      try {{ fmt = new Intl.NumberFormat(locale, {{ style: "currency", currency: cur }}); }}
      catch (e2) {{ fmt = {{ format: function (v) {{ return v.toFixed(2) + " " + cur; }} }}; }}
    }}
  }}
  var els = document.querySelectorAll(".price[data-cur]");
  for (var i = 0; i < els.length; i++) {{
    var el = els[i], src = el.getAttribute("data-cur"), amt = parseFloat(el.getAttribute("data-amount"));
    if (!R[src] || isNaN(amt)) continue;
    var v = src === cur ? amt : amt / R[src] * R[cur];
    el.textContent = (src === cur ? "" : "≈ ") + fmt.format(v);
  }}
}})();
</script>
</body>
</html>
"""


def load_video_log() -> list[dict]:
    try:
        return json.loads(VIDEO_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def to_lei(p: dict, rates: dict) -> float:
    if p["cur"] == "RON":
        return p["amount"]
    return p["amount"] / rates.get(p["cur"], 1) * rates.get("RON", 4.97)


def check_voice() -> None:
    try:
        import make_clips
        with tempfile.TemporaryDirectory() as d:
            words = make_clips._synth_sentence("Bună!", Path(d) / "t.mp3")
        log(f"[info] voce: funcționează ({make_clips.VOICE}, {len(words)} cuvinte sincronizate)")
    except Exception as exc:
        log(f"[warn] voce: indisponibilă azi, clipurile ies fără voce ({str(exc)[:120]})")


def recover_clips(video_log: list[dict]) -> None:
    """Daca memoria GitHub s-a pierdut, recupereaza clipurile recente de pe site."""
    keep_from = (datetime.now(timezone.utc) - timedelta(days=CLIP_FILES_DAYS)).date().isoformat()
    recent = [v for v in video_log if v.get("created", "") >= keep_from]
    if not recent:
        return
    live = {}
    try:
        live = {c["num"]: c for c in requests.get(f"{SITE_URL}/status.json", timeout=20).json().get("clips", [])}
    except Exception:
        pass
    CLIP_CACHE.mkdir(parents=True, exist_ok=True)
    fixed = 0
    for v in recent:
        if not v.get("clip") and v["num"] in live:
            v["clip"] = {**live[v["num"]], "created": v.get("created")}
            fixed += 1
        for f in (f"trendixeu-{v['num']}.mp4", f"trendixeu-{v['num']}.jpg"):
            if not (CLIP_CACHE / f).exists():
                try:
                    r = requests.get(f"{SITE_URL}/clips/{f}", timeout=60)
                    if r.ok and len(r.content) > 1000:
                        (CLIP_CACHE / f).write_bytes(r.content)
                        fixed += 1
                except Exception:
                    pass
    if fixed:
        log(f"[info] clipuri: am recuperat {fixed} fișiere/descrieri de pe site")


def make_daily_clips(hot: list[dict], featured: list[dict], rates: dict, video_log: list[dict]) -> list[dict]:
    """Alege produse noi (fara clip pana acum), randeaza clipurile, le trece in jurnal."""
    if CLIPS_PER_DAY <= 0 or not shutil.which("ffmpeg"):
        log("[warn] clipuri: ffmpeg lipseste sau generarea e oprita")
        return []
    try:
        import make_clips
    except Exception as exc:
        log(f"[warn] clipuri: nu pot incarca generatorul ({exc})")
        return []
    recover_clips(video_log)
    check_voice()
    done = {v["id"] for v in video_log}
    next_num = max([v.get("num", 0) for v in video_log] + [p.get("num", 0) for p in featured] + [0]) + 1
    # produsele cu filmare de la vanzator dau clipuri mult mai bune: au prioritate
    fresh = [p for p in hot if p["id"] not in done]
    with_video = sum(1 for p in fresh if p.get("video"))
    log(f"[info] clipuri: {len(fresh)} produse noi, {with_video} cu filmare de la vanzator")
    todo = sorted(fresh, key=lambda x: (not x.get("video"), -(x.get("orders") or 0)))[:CLIPS_PER_DAY]
    clips_dir = OUT_DIR / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    made = []
    today = datetime.now(timezone.utc).date().isoformat()
    for p in todo:
        num = next_num
        fname = f"trendixeu-{num}.mp4"
        try:
            t0 = time.time()
            copy = make_clips.render_clip(p, num, to_lei(p, rates), clips_dir / fname,
                                          sheet_path=clips_dir / f"sheet-{num}.jpg",
                                          poster_path=clips_dir / f"trendixeu-{num}.jpg")
            next_num += 1
            entry = {k: p.get(k) for k in ("id", "title", "titles", "cur", "amount", "link", "image",
                                           "orders", "rating", "kw")}
            entry.update({"num": num, "created": today, "section": "clip"})
            clip_info = {**copy, "num": num, "file": f"clips/{fname}", "poster": f"clips/trendixeu-{num}.jpg",
                         "created": today, "id": p["id"], "link": p["link"],
                         "image": p["image"], "title_ro": (p.get("titles") or {}).get("ro") or p["title"]}
            entry["clip"] = clip_info
            video_log.append(entry)
            made.append(clip_info)
            CLIP_CACHE.mkdir(parents=True, exist_ok=True)
            for f in (fname, f"trendixeu-{num}.jpg"):
                if (clips_dir / f).exists():
                    shutil.copy(clips_dir / f, CLIP_CACHE / f)
            log(f"[info] clip #{num} gata in {time.time() - t0:.0f}s, {copy.get('duration')}s"
                + (", cu filmarea vanzatorului" if copy.get("used_seller_video") else ", din poze animate")
                + (", cu voce" if copy.get("voiced") else ", FARA voce"))
        except Exception as exc:
            log(f"[warn] clip pentru {p['id']} esuat: {str(exc)[:200]}")
    if not todo:
        log("[info] clipuri: niciun produs nou azi (toate au deja clip)")
    # clipurile din ultimele zile raman disponibile in consola
    keep_from = (datetime.now(timezone.utc) - timedelta(days=CLIP_FILES_DAYS)).date().isoformat()
    keep = {v["num"] for v in video_log if v.get("created", "") >= keep_from}
    if CLIP_CACHE.exists():
        for f in CLIP_CACHE.iterdir():
            m = re.match(r"trendixeu-(\d+)\.(mp4|jpg)$", f.name)
            if not m:
                continue
            if int(m.group(1)) in keep:
                if not (clips_dir / f.name).exists():
                    shutil.copy(f, clips_dir / f.name)
            else:
                f.unlink()
    VIDEO_LOG_PATH.write_text(json.dumps(video_log, ensure_ascii=False, indent=1), encoding="utf-8")
    recent = [v["clip"] for v in reversed(video_log)
              if v.get("clip") and v["num"] in keep and (clips_dir / f"trendixeu-{v['num']}.mp4").exists()]
    return recent


def main() -> None:
    started = datetime.now(timezone.utc)
    featured = [normalize_price(p) for p in json.loads(FEATURED_PATH.read_text(encoding="utf-8"))]
    rates = get_rates()
    orders = {"days": 30, "items": [], "error": None}
    try:
        orders = fetch_orders()
    except Exception as exc:
        log(f"[warn] nu am putut citi comenzile: {exc}")
    hot = []
    try:
        hot = fetch_hot_products()
    except Exception as exc:  # nu opri publicarea produselor fixe daca API-ul pica
        log(f"[warn] nu am putut lua produsele hot: {exc}")

    load_tr_cache()
    localize(featured, "ro")
    localize(hot, "en")
    save_tr_cache()
    st = _tr_stats
    level = "warn" if st["failed"] or st["skipped"] else "info"
    log(f"[{level}] traduceri: {st['new']} noi, {st['cache']} din memorie"
        + (f", {st['failed']} esuate" if st["failed"] else "")
        + (f", {st['skipped']} amanate (limita zilnica)" if st["skipped"] else ""))

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir()
    if IMG_DIR.exists():
        shutil.copytree(IMG_DIR, OUT_DIR / "img")

    video_log = load_video_log()
    clips = make_daily_clips(hot, featured, rates, video_log)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CLIP_PRODUCTS_DAYS)).date().isoformat()
    clip_items = [normalize_price(dict(v)) for v in reversed(video_log) if v.get("created", "") >= cutoff]
    clip_ids = {v["id"] for v in clip_items}
    hot_rest = [p for p in hot if p["id"] not in clip_ids]
    (OUT_DIR / "index.html").write_text(render(featured, hot_rest, rates, clip_items), encoding="utf-8")
    for static in ("track.js", "i18n.js"):
        if (ROOT / "site" / static).exists():
            shutil.copy(ROOT / "site" / static, OUT_DIR / static)
    console_src = ROOT / "site" / "console.html"
    if console_src.exists():
        (OUT_DIR / "console").mkdir()
        shutil.copy(console_src, OUT_DIR / "console" / "index.html")
    status = json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "started": started.isoformat(timespec="seconds"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "commit": (os.environ.get("GITHUB_SHA") or "")[:7],
        "featured": len(featured), "hot": len(hot),
        "rates": {k: rates[k] for k in ("RON", "USD", "GBP", "PLN") if k in rates},
        "filters": {"min_rating_percent": MIN_RATING_PERCENT, "min_orders": MIN_ORDERS,
                    "max_hot": MAX_HOT_PRODUCTS, "ship_to": SHIP_TO, "keywords": KEYWORDS},
        "featured_items": [{k: p.get(k) for k in ("id", "title", "titles", "cur", "amount", "link", "image")} for p in featured],
        "hot_items": [{k: p.get(k) for k in ("id", "title", "titles", "cur", "amount", "link", "image", "meta", "orders", "rating")} for p in hot],
        "orders": orders,
        "clips": clips,
        "clip_products": [{k: v.get(k) for k in ("num", "id", "title", "titles", "created", "link", "image")} for v in clip_items],
        "log": LOG,
    }, ensure_ascii=False, indent=1)
    (OUT_DIR / "status.json").write_text(status, encoding="utf-8")
    run = os.environ.get("GITHUB_RUN_NUMBER")
    if run:
        (OUT_DIR / f"status-{run}.json").write_text(status, encoding="utf-8")
    print(f"OK — {len(featured)} produse fixe + {len(hot)} produse hot")


if __name__ == "__main__":
    main()
