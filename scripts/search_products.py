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
    for q in queries:
        for p in sb.query_products(q, tracking):
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
