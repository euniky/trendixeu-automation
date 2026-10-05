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
                "video_url": p.get("product_video_url") or "",
                "cats": [p.get("first_level_category_name"), p.get("second_level_category_name")],
            })
    out.sort(key=lambda x: -x["orders"])
    import subprocess
    Path("/tmp/p/vid").mkdir(parents=True, exist_ok=True)
    for x in out:   # descarc filmarea, ii citesc rezolutia si salvez 4 cadre ca s-o vad
        if not x.get("video_url"):
            continue
        try:
            r = sb.requests.get(x["video_url"], timeout=90, headers={"User-Agent": "Mozilla/5.0"})
            f = Path(f"/tmp/v_{x['id']}.mp4")
            f.write_bytes(r.content)
            q = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height:format=duration", "-of", "default=nw=1", str(f)],
                               capture_output=True, text=True, timeout=60)
            vals = dict(l.split("=", 1) for l in q.stdout.split() if "=" in l)
            x["video_res"] = f"{vals.get('width', '?')}x{vals.get('height', '?')}"
            x["video_sec"] = vals.get("duration")
            x["video_mb"] = round(len(r.content) / 1e6, 1)
            dur = float(vals.get("duration") or 8)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(f), "-vf",
                            f"fps=4/{dur:.2f},scale=-2:200,tile=4x1", "-frames:v", "1",
                            f"/tmp/p/vid/{x['id']}.jpg"], timeout=120)
        except Exception as exc:
            x["video_res"] = f"eroare: {str(exc)[:60]}"
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
