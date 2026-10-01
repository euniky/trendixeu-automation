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
import shutil
import sys
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
KEYWORDS = ["home gadget", "cleaning", "kitchen", "phone accessories"]  # cautari, pe rand


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
    for kw in KEYWORDS:
        for p in query_products(kw, tracking_id):
            pid = p.get("product_id")
            if pid in seen or not p.get("promotion_link"):
                continue
            rating = float(str(p.get("evaluate_rate", "0")).rstrip("%") or 0)
            orders = int(p.get("lastest_volume") or 0)
            if rating < MIN_RATING_PERCENT or orders < MIN_ORDERS:
                continue
            seen.add(pid)
            picked.append({
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
    meta = f'<p class="meta">{e(p["meta"])}</p>' if p.get("meta") else ""
    return f"""
  <a class="card" href="{e(p['link'])}" target="_blank" rel="noopener">
    <img class="thumb" src="{e(p['image'])}" alt="" loading="lazy">
    <div class="info">
      <p class="title">{e(p['title'])}</p>
      {meta}
      <div class="row"><span class="price" data-cur="{p['cur']}" data-amount="{p['amount']}">{lei_text(p, rates)}</span><span class="buy">Cumpără · Buy</span></div>
    </div>
  </a>"""


def render(featured: list[dict], hot: list[dict], rates: dict) -> str:
    now = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    hot_section = ""
    if hot:
        hot_section = f"""
  <h2 class="section-title">Hot deals · actualizat {now}</h2>
  {''.join(card_html(p, rates) for p in hot)}"""
    return f"""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>@trendixeu — Oferte zilnice / Daily deals</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
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
.thumb{{width:96px;height:96px;object-fit:cover;border-radius:12px;flex-shrink:0;background:#333;}}
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
  <p class="tag">Oferte zilnice · Daily deals</p>
  <h2 class="section-title">Din clipurile mele</h2>
  {''.join(card_html(p, rates) for p in featured)}
  {hot_section}
</div>
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


def main() -> None:
    started = datetime.now(timezone.utc)
    featured = [normalize_price(p) for p in json.loads(FEATURED_PATH.read_text(encoding="utf-8"))]
    rates = get_rates()
    hot = []
    try:
        hot = fetch_hot_products()
    except Exception as exc:  # nu opri publicarea produselor fixe daca API-ul pica
        log(f"[warn] nu am putut lua produsele hot: {exc}")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir()
    if IMG_DIR.exists():
        shutil.copytree(IMG_DIR, OUT_DIR / "img")
    (OUT_DIR / "index.html").write_text(render(featured, hot, rates), encoding="utf-8")
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
        "featured_items": [{k: p.get(k) for k in ("title", "cur", "amount", "link", "image")} for p in featured],
        "hot_items": [{k: p.get(k) for k in ("title", "cur", "amount", "link", "image", "meta")} for p in hot],
        "log": LOG,
    }, ensure_ascii=False, indent=1)
    (OUT_DIR / "status.json").write_text(status, encoding="utf-8")
    run = os.environ.get("GITHUB_RUN_NUMBER")
    if run:
        (OUT_DIR / f"status-{run}.json").write_text(status, encoding="utf-8")
    print(f"OK — {len(featured)} produse fixe + {len(hot)} produse hot")


if __name__ == "__main__":
    main()
