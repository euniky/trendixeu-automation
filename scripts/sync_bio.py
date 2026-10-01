"""
Reconstruieste automat pagina de bio (index.html) cu:
  - produsele "fixe" din data/featured_products.json (cele din videoclipuri, editate manual de tine)
  - produsele "hot sales" preluate live din AliExpress Affiliate API (filtrate dupa rating/comenzi)

Ruleaza zilnic prin GitHub Actions (.github/workflows/daily-refresh.yml).
Are nevoie de urmatoarele variabile de mediu (secrets in GitHub):
  ALI_APP_KEY, ALI_APP_SECRET, ALI_TRACKING_ID
"""
import json
import os
import sys
from pathlib import Path

from aliexpress_api import AliexpressApi, models

ROOT = Path(__file__).resolve().parent.parent
FEATURED_PATH = ROOT / "data" / "featured_products.json"
OUTPUT_PATH = ROOT / "public" / "index.html"

# ---- reguli de filtrare pentru produsele "hot sales" ----
MIN_RATING = 4.5          # rating minim (evaluate_rate)
MIN_ORDERS = 300          # comenzi minime
MAX_HOT_PRODUCTS = 10     # cate produse hot afisam sub cele fixe
CATEGORY_IDS = None       # ex: [7] pentru "Home & Garden" - lasa None pentru toate categoriile


def get_client() -> AliexpressApi:
    key = os.environ["ALI_APP_KEY"]
    secret = os.environ["ALI_APP_SECRET"]
    tracking_id = os.environ["ALI_TRACKING_ID"]
    return AliexpressApi(key, secret, models.Language.RO, models.Currency.RON, tracking_id)


def fetch_hot_products(client: AliexpressApi) -> list[dict]:
    """Ia produsele hot, le filtreaza dupa rating/comenzi si genereaza linkuri afiliate."""
    kwargs = {"page_size": 50}
    if CATEGORY_IDS:
        kwargs["category_ids"] = CATEGORY_IDS
    raw = client.get_hotproducts(**kwargs)
    items = getattr(raw, "products", raw)  # unele versiuni intorc un obiect cu .products

    filtered = []
    for p in items:
        rating = float(getattr(p, "evaluate_rate", "0").rstrip("%") or 0) / 20  # ex: 96% -> ~4.8
        orders = int(getattr(p, "lastest_volume", getattr(p, "volume", 0)) or 0)
        if rating < MIN_RATING or orders < MIN_ORDERS:
            continue
        filtered.append(p)
        if len(filtered) >= MAX_HOT_PRODUCTS:
            break

    if not filtered:
        return []

    ids = [str(getattr(p, "product_id")) for p in filtered]
    links = client.get_affiliate_links(ids)
    link_map = {}
    for link in links:
        # aliexpress-api intoarce obiecte cu .source_value si .promotion_link
        src = getattr(link, "source_value", None)
        if src:
            link_map[str(src)] = link.promotion_link

    out = []
    for p in filtered:
        pid = str(getattr(p, "product_id"))
        out.append({
            "title": getattr(p, "product_title", "Produs"),
            "price": f"RON {getattr(p, 'target_sale_price', '?')}",
            "link": link_map.get(pid) or getattr(p, "promotion_link", "#"),
            "image": getattr(p, "product_main_image_url", ""),
        })
    return out


def load_featured() -> list[dict]:
    return json.loads(FEATURED_PATH.read_text(encoding="utf-8"))


def card_html(p: dict) -> str:
    return f"""
  <a class="card" href="{p['link']}" target="_blank" rel="noopener">
    <img class="thumb" src="{p['image']}" alt="" loading="lazy">
    <div class="info">
      <p class="title">{p['title']}</p>
      <div class="row"><span class="price">{p['price']}</span><span class="buy">Cumpără · Buy</span></div>
    </div>
  </a>"""


def render(featured: list[dict], hot: list[dict]) -> str:
    featured_html = "\n".join(card_html(p) for p in featured)
    hot_html = "\n".join(card_html(p) for p in hot)
    hot_section = f"""
  <h2 class="section-title">🔥 Hot sales — actualizat automat</h2>
  {hot_html}""" if hot else ""

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
body{{background:linear-gradient(180deg,var(--bg),var(--bg2));color:var(--text);font-family:Inter,sans-serif;padding:24px 16px 60px;max-width:480px;margin:0 auto;}}
h1{{font-family:'Space Grotesk',sans-serif;font-size:22px;text-align:center;margin:14px 0 4px;}}
.section-title{{font-size:15px;color:var(--muted);margin:28px 0 12px;}}
.card{{display:flex;gap:14px;background:var(--card);border-radius:16px;padding:12px;margin-bottom:12px;text-decoration:none;color:var(--text);align-items:center;}}
.thumb{{width:96px;height:96px;object-fit:cover;border-radius:12px;flex-shrink:0;background:#333;}}
.title{{font-size:14px;line-height:1.3;margin-bottom:8px;}}
.row{{display:flex;justify-content:space-between;align-items:center;}}
.price{{color:var(--gold);font-weight:700;font-size:15px;}}
.buy{{background:var(--hot);color:#fff;border-radius:999px;padding:6px 14px;font-size:13px;font-weight:600;}}
</style>
</head>
<body>
<h1>@trendixeu — oferte zilnice</h1>
{featured_html}
{hot_section}
</body>
</html>
"""


def main() -> None:
    featured = load_featured()
    hot = []
    try:
        client = get_client()
        hot = fetch_hot_products(client)
    except Exception as exc:  # nu opri publicarea produselor fixe daca API-ul pica
        print(f"[warn] nu am putut lua produsele hot: {exc}", file=sys.stderr)

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(render(featured, hot), encoding="utf-8")
    print(f"OK — {len(featured)} produse fixe + {len(hot)} produse hot -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
