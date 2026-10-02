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
import math
import os
import re
import shutil
import subprocess
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
CLIPS_PER_DAY = int(os.environ.get("CLIPS_PER_DAY", "6"))
CLIP_PRODUCTS_DAYS = 30                               # cat timp raman pe pagina produsele din clipuri
CLIP_VOICE = os.environ.get("CLIP_VOICE", "none")     # none = fara voce; edge = voce gratuita; elevenlabs = premium
CLIP_MUSIC = os.environ.get("CLIP_MUSIC", "on") != "off"   # muzica de fundal in clipuri
CLIP_FILES_DAYS = 7                                   # cat timp raman clipurile in consola
CLIP_CACHE = ROOT / "data" / ".cache" / "clips"       # pastrate intre rulari (cache GitHub)
MIN_FALLBACK_SCORE = 3   # pragul minim cand nu sunt destule produse "wow"
MUSIC_DIR = ROOT / "data" / ".cache" / "music"
# muzica energica, fara drepturi de platit (Kevin MacLeod, CC BY 4.0: cere doar mentionarea autorului)
MUSIC_WANTED = ["Funky Chunk", "Life of Riley", "Sneaky Snitch", "Fluffing a Duck", "Monkeys Spinning Monkeys",
                "Pamgaea", "Big Mojo", "Blip Stream", "Awesome Call", "Funkorama", "Cipher", "Happy Alley",
                "Carefree", "Hep Cats", "Wallpaper", "Local Forecast"]
MUSIC_CREDIT = "🎵 {title} – Kevin MacLeod (incompetech.com) · CC BY 4.0"
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
    for attempt in range(4):
        params["timestamp"] = str(int(time.time() * 1000))
        params.pop("sign", None)
        params["sign"] = sign(secret, params)
        r = requests.post(API_URL, data=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "ApiCallLimit" in json.dumps(data.get("error_response", "")) and attempt < 3:
            time.sleep(2 + 2 * attempt)   # limita de frecventa AliExpress: astept si reincerc
            continue
        break
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


CANDIDATES: list[dict] = []   # toate produsele bune gasite azi (din ele se aleg clipurile)


def fetch_hot_products() -> list[dict]:
    tracking_id = os.environ["ALI_TRACKING_ID"].strip()
    seen, picked = set(), []
    day = datetime.now(timezone.utc).toordinal()
    order = KEYWORDS[day % len(KEYWORDS):] + KEYWORDS[:day % len(KEYWORDS)]
    for kw in order:
        taken = 0
        for p in query_products(kw, tracking_id):
            pid = p.get("product_id")
            if pid in seen or not p.get("promotion_link"):
                continue
            rating = float(str(p.get("evaluate_rate", "0")).rstrip("%") or 0)
            orders = int(p.get("lastest_volume") or 0)
            if rating < MIN_RATING_PERCENT or orders < MIN_ORDERS:
                continue
            seen.add(pid)
            smalls = p.get("product_small_image_urls") or []
            if isinstance(smalls, dict):
                smalls = smalls.get("string") or []
            item = {
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
            }
            CANDIDATES.append(item)
            if taken < PER_KEYWORD and len(picked) < MAX_HOT_PRODUCTS:
                picked.append(item)
                taken += 1
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


def recover_from_artifacts(missing: set[int]) -> int:
    """Descarca clipurile lipsa din copiile de rezerva GitHub (ultimele 7 zile)."""
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo and missing):
        return 0
    import io
    import zipfile
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    got = 0
    try:
        arts = requests.get(f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=50", headers=hdr, timeout=30).json()
        for a in arts.get("artifacts", []):
            if not missing or a.get("expired") or not a["name"].startswith("clipuri-"):
                continue
            z = requests.get(a["archive_download_url"], headers=hdr, timeout=120)
            if not z.ok:
                continue
            with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
                for name in zf.namelist():
                    m = re.match(r"(?:.*/)?trendixeu-(\d+)\.mp4$", name)
                    if m and int(m.group(1)) in missing:
                        (CLIP_CACHE / f"trendixeu-{m.group(1)}.mp4").write_bytes(zf.read(name))
                        missing.discard(int(m.group(1)))
                        got += 1
    except Exception as exc:
        log(f"[warn] recuperare din copiile de rezerva: {str(exc)[:120]}")
    return got


WOW_WORDS = {
    "robot": 4, "automatic": 3, "auto ": 2, "smart": 2, "magic": 3, "lazy": 3, "self-": 2, "electric": 2,
    "projector": 3, "levitat": 4, "galaxy": 3, "laser": 2, "heated": 2, "massag": 2, "humidifier": 2,
    "vacuum": 2, "sealer": 2, "dispenser": 2, "slicer": 2, "chopper": 2, "spray": 1, "steam": 2,
    "rechargeable": 2, "wireless": 2, "cordless": 2, "bluetooth": 1, "magnetic": 2, "foldable": 2,
    "retractable": 2, "2 in 1": 2, "3 in 1": 2, "4 in 1": 2, "multifunction": 1, "multi-function": 1,
    "creative": 2, "funny": 2, "cute": 1, "mini": 1, "portable": 1, "led": 2, "rgb": 2, "light": 1,
    "camera": 2, "drone": 3, "fan": 1, "organizer": 1, "gadget": 2, "artifact": 2, "hack": 2, "360": 1,
}
BORING_WORDS = {
    "swab": -5, "cotton": -3, "glove": -4, "replacement": -5, "spare": -5, "refill": -5, "screw": -5,
    "sticker": -3, "cable": -3, "case": -2, "cover": -2, "sock": -4, "brush set": -2, "makeup brush": -3,
    "filter": -4, "pcs": -1, "pack": -1, "accessories": -1, "nail": -2, "tweezer": -2, "hair tie": -4,
    "earring": -3, "ring": -1, "necklace": -3, "label": -3, "tape": -2,
}
MIN_WOW_SCORE = 5        # sub acest scor produsul nu primeste clip


def wow_score(p: dict, lei: float) -> tuple[float, list[str]]:
    """Cat de probabil e ca produsul sa opreasca scroll-ul pe TikTok."""
    t = " " + p["title"].lower() + " "
    score, why = 0.0, []
    for w, v in {**WOW_WORDS, **BORING_WORDS}.items():
        if w in t:
            score += v
            why.append(f"{w.strip()}{v:+d}")
    if p.get("video"):
        score += 3
        why.append("filmare+3")
    orders = int(p.get("orders") or 0)
    if orders >= 1000:
        pts = round(min(3.0, math.log10(orders) - 2), 1)
        score += pts
        why.append(f"comenzi+{pts}")
    if float(p.get("rating") or 0) >= 96:
        score += 1
        why.append("rating+1")
    if lei <= 60:
        score += 1
        why.append("pret-mic+1")
    elif lei > 250:
        score -= 2
        why.append("scump-2")
    return round(score, 1), why


def product_details(pid: str) -> dict:
    """Pozele si filmarea unui produs anume (pentru refacerea unui clip)."""
    try:
        data = call_api("aliexpress.affiliate.productdetail.get", {
            "product_ids": pid.lstrip("p"), "target_currency": CURRENCY, "target_language": "EN",
            "tracking_id": os.environ["ALI_TRACKING_ID"].strip(), "country": SHIP_TO})
        res = data.get("aliexpress_affiliate_productdetail_get_response", {}).get("resp_result", {}).get("result") or {}
        prods = (res.get("products") or {}).get("product") or []
        if prods:
            smalls = prods[0].get("product_small_image_urls") or []
            if isinstance(smalls, dict):
                smalls = smalls.get("string") or []
            return {"images": [u for u in smalls if isinstance(u, str)][:5], "video": prods[0].get("product_video_url") or ""}
    except Exception as exc:
        log(f"[warn] detalii produs {pid}: {str(exc)[:120]}")
    return {}


FEATURED_IDS = ROOT / "data" / ".cache" / "featured_ids.json"


def resolve_product_id(link: str) -> str | None:
    """Linkul scurt de afiliere (s.click...) -> ID-ul produsului AliExpress."""
    try:
        r = requests.get(link, timeout=30, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126 Mobile"})
        for u in [h.headers.get("Location", "") for h in r.history] + [r.url, r.text[:20000]]:
            m = re.search(r"(?:item|i)/(\d{8,})\.html|productIds?=(\d{8,})|item%2F(\d{8,})", requests.utils.unquote(u or ""))
            if m:
                return next(g for g in m.groups() if g)
    except Exception as exc:
        log(f"[warn] nu am putut deschide {link}: {str(exc)[:100]}")
    return None


def refresh_featured(featured: list[dict]) -> None:
    """Pretul actual (si pozele/filmarea) pentru produsele adaugate manual in pagina de bio."""
    ids = json.loads(FEATURED_IDS.read_text(encoding="utf-8")) if FEATURED_IDS.exists() else {}
    for p in featured:
        if not ids.get(p["link"]):
            pid = resolve_product_id(p["link"])
            if pid:
                ids[p["link"]] = pid
    FEATURED_IDS.parent.mkdir(parents=True, exist_ok=True)
    FEATURED_IDS.write_text(json.dumps(ids, indent=1), encoding="utf-8")
    pids = [ids[p["link"]] for p in featured if ids.get(p["link"])]
    if not pids:
        log("[warn] preturi produse fixe: nu am gasit ID-urile produselor")
        return
    found = {}
    try:
        data = call_api("aliexpress.affiliate.productdetail.get", {
            "product_ids": ",".join(pids), "target_currency": CURRENCY, "target_language": "EN",
            "tracking_id": os.environ["ALI_TRACKING_ID"].strip(), "country": SHIP_TO})
        res = data.get("aliexpress_affiliate_productdetail_get_response", {}).get("resp_result", {}).get("result") or {}
        for x in (res.get("products") or {}).get("product") or []:
            found[str(x.get("product_id"))] = x
    except Exception as exc:
        log(f"[warn] preturi produse fixe: {str(exc)[:150]}")
        return
    changed = []
    for p in featured:
        x = found.get(ids.get(p["link"], ""))
        if not x:
            if ids.get(p["link"]):
                log(f"[warn] produsul fix #{p.get('num')} nu mai apare in AliExpress (posibil indisponibil)")
            continue
        old = p.get("amount"), p.get("cur")
        price = float(x.get("target_sale_price") or x.get("sale_price") or 0)
        if price <= 0:
            continue
        smalls = x.get("product_small_image_urls") or []
        if isinstance(smalls, dict):
            smalls = smalls.get("string") or []
        p.update({"pid": ids[p["link"]], "cur": CURRENCY, "amount": price,
                  "images": [u for u in smalls if isinstance(u, str)][:5],
                  "image_url": x.get("product_main_image_url") or "",
                  "video": x.get("product_video_url") or "",
                  "orders": int(x.get("lastest_volume") or 0),
                  "rating": float(str(x.get("evaluate_rate", "0")).rstrip("%") or 0)})
        changed.append(f"#{p.get('num')} {old[1]} {old[0]} -> {CURRENCY} {price}")
    log(f"[info] preturi produse fixe actualizate: {len(changed)}/{len(featured)}"
        + (" (" + "; ".join(changed) + ")" if changed else ""))


def voice_demo(video_log: list[dict], rates: dict) -> dict | None:
    """Acelasi clip, de doua ori: voce gratuita vs voce premium (ElevenLabs)."""
    num = os.environ.get("DEMO_PREMIUM", "").strip()
    demo_json = CLIP_CACHE / "demo.json"
    clips_dir = OUT_DIR / "clips"
    if not num:
        if demo_json.exists():   # comparatia ramane vizibila pana la una noua
            for f in CLIP_CACHE.glob("demo-*"):
                shutil.copy(f, clips_dir / f.name)
            return json.loads(demo_json.read_text(encoding="utf-8"))
        return None
    entry = next((v for v in video_log if str(v.get("num")) == num), None)
    if not entry:
        log(f"[warn] comparatie voce: produsul #{num} nu exista in jurnal")
        return None
    import make_clips
    p = dict(entry)
    if not p.get("images"):
        p.update(product_details(p["id"]))
    lei = to_lei(normalize_price(dict(entry)), rates)
    out = {"num": int(num), "title_ro": (p.get("titles") or {}).get("ro") or p["title"], "versions": []}
    for provider, label in (("edge", "Voce gratuită (Microsoft)"), ("elevenlabs", "Voce premium (ElevenLabs)")):
        fname = f"demo-{num}-{provider}.mp4"
        try:
            info = make_clips.render_clip(p, int(num), lei, clips_dir / fname, voice=provider,
                                          poster_path=clips_dir / f"demo-{num}-{provider}.jpg")
            ok = info.get("voice") == provider
            out["versions"].append({"provider": provider, "label": label, "file": f"clips/{fname}",
                                    "poster": f"clips/demo-{num}-{provider}.jpg", "ok": ok,
                                    "error": info.get("voice_error"), "chars": sum(len(x) for x in info.get("script", []))})
            log(f"[{'info' if ok else 'warn'}] comparatie voce #{num}: {label} "
                + ("gata" if ok else f"esuata ({info.get('voice_error')})"))
        except Exception as exc:
            log(f"[warn] comparatie voce #{num} ({provider}): {str(exc)[:200]}")
    for f in clips_dir.glob(f"demo-{num}-*"):
        shutil.copy(f, CLIP_CACHE / f.name)
    demo_json.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def get_music() -> list[Path]:
    """Muzica de fundal: o descarca o singura data de pe Internet Archive si o tine in cache."""
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    have = sorted(MUSIC_DIR.glob("*.mp3"))
    if len(have) >= 6:
        return have
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    wanted = {norm(w): w for w in MUSIC_WANTED}
    try:
        meta = requests.get("https://archive.org/metadata/Incompetech", timeout=60).json()
        for f in meta.get("files", []):
            name = f.get("name", "")
            if not name.lower().endswith(".mp3"):
                continue
            title = wanted.get(norm(Path(name).stem))
            dest = MUSIC_DIR / f"{title}.mp3" if title else None
            if not dest or dest.exists():
                continue
            r = requests.get(f"https://archive.org/download/Incompetech/{requests.utils.quote(name)}", timeout=120)
            if r.ok and len(r.content) > 50_000:
                dest.write_bytes(r.content)
    except Exception as exc:
        log(f"[warn] muzica: nu am putut descarca melodiile ({str(exc)[:150]})")
    have = sorted(MUSIC_DIR.glob("*.mp3"))
    log(f"[info] muzica: {len(have)} melodii disponibile" + (f" ({', '.join(p.stem for p in have)})" if have else ""))
    return have


def make_daily_clips(hot: list[dict], featured: list[dict], rates: dict, video_log: list[dict]) -> list[dict]:
    """Recupereaza clipurile recente, randeaza clipuri noi, intoarce lista pentru consola."""
    clips_dir = OUT_DIR / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    CLIP_CACHE.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    keep_from = (datetime.now(timezone.utc) - timedelta(days=CLIP_FILES_DAYS)).date().isoformat()

    try:
        import make_clips
    except Exception as exc:
        log(f"[warn] clipuri: nu pot incarca generatorul ({exc})")
        make_clips = None

    # --- 1. recuperare: clipurile din ultimele zile trebuie sa ramana in consola
    recover_clips(video_log)
    missing = {v["num"] for v in video_log if v.get("created", "") >= keep_from
               and not (CLIP_CACHE / f"trendixeu-{v['num']}.mp4").exists()}
    if missing and recover_from_artifacts(missing):
        log("[info] clipuri: recuperate din copiile de rezerva GitHub")
    for v in video_log:
        if v.get("created", "") < keep_from:
            continue
        mp4, jpg = CLIP_CACHE / f"trendixeu-{v['num']}.mp4", CLIP_CACHE / f"trendixeu-{v['num']}.jpg"
        if mp4.exists() and not jpg.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", str(mp4), "-frames:v", "1",
                            "-vf", "scale=540:960", str(jpg)], capture_output=True)
        if not v.get("clip") and make_clips is not None and mp4.exists():
            info = make_clips.build_copy(v, v["num"], to_lei(normalize_price(dict(v)), rates))
            info.pop("sentences", None)
            v["clip"] = {**info, "num": v["num"], "file": f"clips/trendixeu-{v['num']}.mp4",
                         "poster": f"clips/trendixeu-{v['num']}.jpg", "created": v.get("created"),
                         "id": v["id"], "link": v["link"], "image": v.get("image"),
                         "title_ro": (v.get("titles") or {}).get("ro") or v["title"]}

    # --- 1b. refacere cu muzica a unor clipuri deja facute (REMAKE="13,14")
    remake = {int(x) for x in re.findall(r"\d+", os.environ.get("REMAKE", ""))}
    have_nums = {v.get("num") for v in video_log}
    for fp in featured:
        if fp.get("num") in remake and fp.get("num") not in have_nums and fp.get("image_url"):
            entry = {k: fp.get(k) for k in ("id", "title", "titles", "cur", "amount", "link", "images",
                                            "video", "orders", "rating")}
            entry.update({"image": fp["image_url"], "num": fp["num"], "created": today, "section": "featured"})
            if not entry.get("images"):
                entry.update(product_details(fp.get("pid", "")))
            video_log.append(entry)
            log(f"[info] clip nou pentru produsul fix #{fp['num']}"
                + (" (cu filmarea vanzatorului)" if entry.get("video") else ""))
    if remake and make_clips is not None and shutil.which("ffmpeg"):
        music = get_music()
        for v in video_log:
            if v.get("num") not in remake or not music:
                continue
            num, fname = v["num"], f"trendixeu-{v['num']}.mp4"
            track = music[num % len(music)]
            try:
                p = normalize_price(dict(v))
                copy = make_clips.render_clip(p, num, to_lei(p, rates), clips_dir / fname,
                                              sheet_path=clips_dir / f"sheet-{num}.jpg",
                                              poster_path=clips_dir / f"trendixeu-{num}.jpg",
                                              voice=CLIP_VOICE, music=track)
                if copy.get("with_music"):
                    credit = MUSIC_CREDIT.format(title=track.stem)
                    copy["music"] = track.stem
                    for k in ("description_ro", "description_en"):
                        if copy.get(k):
                            copy[k] = copy[k].rstrip() + "\n" + credit
                old = v.get("clip") or {}
                v["clip"] = {**old, **copy, "num": num, "file": f"clips/{fname}",
                             "poster": f"clips/trendixeu-{num}.jpg", "created": old.get("created") or v.get("created"),
                             "id": v["id"], "link": v["link"], "image": v.get("image"),
                             "title_ro": old.get("title_ro") or (v.get("titles") or {}).get("ro") or v["title"]}
                v["remade"] = v.get("remade", 0) + 1
                for f in (fname, f"trendixeu-{num}.jpg"):
                    if (clips_dir / f).exists():
                        shutil.copy(clips_dir / f, CLIP_CACHE / f)
                log(f"[info] clip #{num} refacut cu muzica {track.stem}")
            except Exception as exc:
                log(f"[warn] refacerea clipului #{num} a esuat: {str(exc)[:200]}")

    # --- 2. clipuri noi
    if CLIP_VOICE != "none":
        check_voice()
    if make_clips is not None and CLIPS_PER_DAY > 0 and shutil.which("ffmpeg"):
        done = {v["id"] for v in video_log}
        next_num = max([v.get("num", 0) for v in video_log] + [p.get("num", 0) for p in featured] + [0]) + 1
        pool = CANDIDATES or hot
        fresh = [p for p in pool if p["id"] not in done]
        scored = sorted(((wow_score(p, to_lei(p, rates)), p) for p in fresh), key=lambda x: -x[0][0])
        todo = [p for (sc_, why), p in scored if sc_ >= MIN_WOW_SCORE][:CLIPS_PER_DAY]
        if len(todo) < CLIPS_PER_DAY:
            # zi slaba: completez cu urmatoarele cele mai interesante produse, dar nu cu cele plictisitoare
            extra = [p for (sc_, why), p in scored if MIN_FALLBACK_SCORE <= sc_ < MIN_WOW_SCORE]
            todo += extra[:CLIPS_PER_DAY - len(todo)]
        with_video = sum(1 for p in fresh if p.get("video"))
        log(f"[info] clipuri: {len(fresh)} produse verificate, {with_video} cu filmare, "
            f"{sum(1 for (sc_, _), _p in scored if sc_ >= MIN_WOW_SCORE)} peste pragul de interes ({MIN_WOW_SCORE})")
        for (sc_, why), p in scored[:5]:
            log(f"[info] scor {sc_:>4}: {p['title'][:48]} ({', '.join(why[:5])})")
        if not todo:
            log("[info] clipuri: niciun produs destul de interesant azi, nu generez clipuri slabe")
        missing_titles = [p for p in todo if not p.get("titles")]
        if missing_titles:
            localize(missing_titles, "en")
            save_tr_cache()
        music = get_music() if todo and CLIP_MUSIC else []
        wow_of = {p["id"]: sc_ for (sc_, _w), p in scored}
        for p in todo:
            num = next_num
            fname = f"trendixeu-{num}.mp4"
            track = music[num % len(music)] if music else None
            try:
                t0 = time.time()
                copy = make_clips.render_clip(p, num, to_lei(p, rates), clips_dir / fname,
                                              sheet_path=clips_dir / f"sheet-{num}.jpg",
                                              poster_path=clips_dir / f"trendixeu-{num}.jpg", voice=CLIP_VOICE,
                                              music=track)
                if copy.get("with_music") and track:
                    credit = MUSIC_CREDIT.format(title=track.stem)
                    copy["music"] = track.stem
                    for k in ("description_ro", "description_en"):
                        if copy.get(k):
                            copy[k] = copy[k].rstrip() + "\n" + credit
                next_num += 1
                entry = {k: p.get(k) for k in ("id", "title", "titles", "cur", "amount", "link", "image",
                                               "images", "video", "orders", "rating", "kw")}
                entry.update({"num": num, "created": today, "section": "clip", "wow": wow_of.get(p["id"])})
                entry["clip"] = {**copy, "num": num, "file": f"clips/{fname}", "poster": f"clips/trendixeu-{num}.jpg",
                                 "created": today, "id": p["id"], "link": p["link"], "image": p["image"],
                                 "title_ro": (p.get("titles") or {}).get("ro") or p["title"]}
                video_log.append(entry)
                for f in (fname, f"trendixeu-{num}.jpg"):
                    if (clips_dir / f).exists():
                        shutil.copy(clips_dir / f, CLIP_CACHE / f)
                log(f"[info] clip #{num} gata in {time.time() - t0:.0f}s, {copy.get('duration')}s"
                    + (", cu filmarea vanzatorului" if copy.get("used_seller_video") else ", din poze animate")
                    + (f", voce {copy.get('voice')}" if copy.get("voiced") else ", fara voce")
                    + (f", muzica {copy['music']}" if copy.get("music") else ", fara muzica"))
            except Exception as exc:
                log(f"[warn] clip pentru {p['id']} esuat: {str(exc)[:200]}")
    elif CLIPS_PER_DAY > 0:
        log("[warn] clipuri: ffmpeg lipseste")

    # --- 3. publicare: clipurile din ultimele 7 zile
    keep = {v["num"] for v in video_log if v.get("created", "") >= keep_from}
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
    return [v["clip"] for v in reversed(video_log)
            if v.get("clip") and v["num"] in keep and (clips_dir / f"trendixeu-{v['num']}.mp4").exists()]


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

    try:
        refresh_featured(featured)
    except Exception as exc:
        log(f"[warn] preturi produse fixe: {str(exc)[:150]}")

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
    demo = None
    try:
        demo = voice_demo(video_log, rates)
    except Exception as exc:
        log(f"[warn] comparatie voce: {str(exc)[:200]}")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CLIP_PRODUCTS_DAYS)).date().isoformat()
    clip_items = [normalize_price(dict(v)) for v in reversed(video_log) if v.get("created", "") >= cutoff and v.get("section") != "featured"]
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
        app_dir = ROOT / "site" / "console-app"   # manifest, iconite, mod offline: consola se instaleaza ca aplicatie
        if app_dir.exists():
            for f in app_dir.iterdir():
                shutil.copy(f, OUT_DIR / "console" / f.name)
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
        "voice_demo": demo,
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
