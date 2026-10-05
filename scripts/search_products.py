"""Cauta un produs anume pe AliExpress (prin API-ul de afiliere) si scrie rezultatele cu linkuri de afiliere."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sync_bio as sb  # noqa: E402


def main() -> None:
    queries = [q.strip() for q in " ".join(sys.argv[1:]).split("|") if q.strip()]
    tracking = os.environ["ALI_TRACKING_ID"].strip()
    seen, out = set(), []
    def detail(pid):
        d = sb.call_api("aliexpress.affiliate.productdetail.get", {
            "product_ids": pid, "target_currency": sb.CURRENCY, "target_language": "EN",
            "tracking_id": tracking, "country": sb.SHIP_TO})
        res = d.get("aliexpress_affiliate_productdetail_get_response", {}).get("resp_result", {}).get("result") or {}
        return (res.get("products") or {}).get("product") or []

    for q in queries:
        if q.startswith("http") or q.isdigit():   # link sau ID de produs: detaliile lui
            pid = q if q.isdigit() else sb.resolve_product_id(q)
            prods = detail(pid) if pid else []
            for p in prods:
                p["_video_url"] = p.get("product_video_url")
                p["_cats"] = [p.get("first_level_category_name"), p.get("second_level_category_name")]
            found = prods
        else:
            found = sb.query_products(q, tracking)
        for p in found:
            pid = str(p.get("product_id"))
            if pid in seen:
                continue
            seen.add(pid)
            out.append({
                "query": q, "id": pid, "title": p.get("product_title"),
                "price_eur": p.get("target_sale_price"), "orders": int(p.get("lastest_volume") or 0),
                "rating": p.get("evaluate_rate"), "video": bool(p.get("product_video_url")),
                "image": p.get("product_main_image_url"), "link": p.get("promotion_link"),
                "detail": p.get("product_detail_url"),
                "video_url": p.get("product_video_url"),
                "cats": [p.get("first_level_category_name"), p.get("second_level_category_name")],
            })
    out.sort(key=lambda x: -x["orders"])
    Path("/tmp/p/img").mkdir(parents=True, exist_ok=True)
    must = [w for w in os.environ.get("IMG_FILTER", "").lower().split(",") if w]
    picked = [x for x in out if not must or any(w in (x["title"] or "").lower() for w in must)][:24]
    for x in picked:
        try:
            r = sb.requests.get(x["image"], timeout=30)
            if r.ok:
                Path(f"/tmp/p/img/{x['id']}.jpg").write_bytes(r.content)
        except Exception:
            pass
    Path("/tmp/p/search.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} produse gasite")


if __name__ == "__main__":
    main()
