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


def fetch_hot_products() -> list[dict]:
    tracking_id = os.environ["ALI_TRACKING_ID"].strip()
    seen, picked = set(), []
    for kw in KEYWORDS:
        data = call_api("aliexpress.affiliate.hotproduct.query", {
            "keywords": kw,
            "page_size": 50,
            "sort": "LAST_VOLUME_DESC",
            "ship_to_country": SHIP_TO,
            "target_currency": CURRENCY,
            "target_language": "EN",
            "tracking_id": tracking_id,
        })
        resp = data.get("aliexpress_affiliate_hotproduct_query_response", {}).get("resp_result", {})
        if not resp:
            log(f"[warn] raspuns neasteptat: {str(data)[:300]}")
        if str(resp.get("resp_code")) != "200":
            log(f"[warn] '{kw}': {resp.get('resp_code')} {resp.get('resp_msg')}")
            continue
        products = (resp.get("result") or {}).get("products", {}).get("product", []) or []
        log(f"[info] '{kw}': {len(products)} produse primite")
        for p in products:
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
                "price": f"€ {p.get('target_sale_price') or p.get('sale_price', '?')}",
                "link": p["promotion_link"],
                "image": p.get("product_main_image_url", ""),
                "meta": f"{rating:.0f}% recenzii pozitive · {orders}+ comenzi",
            })
            if len(picked) >= MAX_HOT_PRODUCTS:
                return picked
    return picked


def card_html(p: dict) -> str:
    e = html.escape
    meta = f'<p class="meta">{e(p["meta"])}</p>' if p.get("meta") else ""
    return f"""
  <a class="card" href="{e(p['link'])}" target="_blank" rel="noopener">
    <img class="thumb" src="{e(p['image'])}" alt="" loading="lazy">
    <div class="info">
      <p class="title">{e(p['title'])}</p>
      {meta}
      <div class="row"><span class="price">{e(p['price'])}</span><span class="buy">Cumpără · Buy</span></div>
    </div>
  </a>"""


def render(featured: list[dict], hot: list[dict]) -> str:
    now = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    hot_section = ""
    if hot:
        hot_section = f"""
  <h2 class="section-title">Hot deals · actualizat {now}</h2>
  {''.join(card_html(p) for p in hot)}"""
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
  {''.join(card_html(p) for p in featured)}
  {hot_section}
</div>
</body>
</html>
"""


def main() -> None:
    featured = json.loads(FEATURED_PATH.read_text(encoding="utf-8"))
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
    (OUT_DIR / "index.html").write_text(render(featured, hot), encoding="utf-8")
    (OUT_DIR / "status.json").write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "featured": len(featured), "hot": len(hot), "log": LOG,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK — {len(featured)} produse fixe + {len(hot)} produse hot")


if __name__ == "__main__":
    main()
