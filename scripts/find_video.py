"""Cauta filmarea de prezentare a unui produs direct pe pagina AliExpress (cand API-ul de afiliere nu o da)."""
from __future__ import annotations

import json
import re
import sys

import requests

UA_MOBILE = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/128.0 Mobile Safari/537.36")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0 Safari/537.36")
VIDEO_RE = re.compile(r'https?:\\?/\\?/[^"\'\s<>]*?\.(?:mp4|m3u8)[^"\'\s<>]*', re.I)


def _clean(u: str) -> str:
    return u.replace("\\/", "/").replace("\\u002F", "/").split('"')[0]


def find_video(pid: str, log=print) -> str | None:
    pages = [
        (f"https://www.aliexpress.com/item/{pid}.html", UA_DESKTOP),
        (f"https://m.aliexpress.com/item/{pid}.html", UA_MOBILE),
        (f"https://ro.aliexpress.com/item/{pid}.html", UA_DESKTOP),
    ]
    for url, ua in pages:
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9",
                                                       "Accept": "text/html"}, allow_redirects=True)
        except Exception as exc:
            log(f"[video] {url}: {str(exc)[:80]}")
            continue
        html = r.text
        blocked = any(k in html for k in ("punish", "captcha", "x5secdata", "_____tmd_____"))
        vids = [_clean(m) for m in VIDEO_RE.findall(html)]
        vids = [v for v in vids if "video" in v or "cloudvideo" in v or ".mp4" in v]
        ids = re.findall(r'"videoId"\s*:\s*"?(\d{6,})', html) + re.findall(r'"videoUid"\s*:\s*"([^"]+)"', html)
        log(f"[video] {url} -> {r.status_code}, {len(html)} octeti, blocat={blocked}, "
            f"mp4={len(vids)}, id-uri={ids[:3]}")
        for v in vids:
            try:
                h = requests.head(v, timeout=20, allow_redirects=True, headers={"User-Agent": ua})
                if h.ok and "video" in h.headers.get("Content-Type", ""):
                    return v
            except Exception:
                pass
        for vid in ids:
            if vid.isdigit():
                for cand in (f"https://video.aliexpress-media.com/play/u/ae_sg_item/0/p/1/e/6/t/10301/{vid}.mp4",
                             f"https://cloud.video.taobao.com/play/u/null/p/1/e/6/t/1/{vid}.mp4"):
                    try:
                        h = requests.head(cand, timeout=20, allow_redirects=True)
                        log(f"[video] incerc {cand} -> {h.status_code}")
                        if h.ok:
                            return cand
                    except Exception:
                        pass
    return None


if __name__ == "__main__":
    out = {}
    for arg in sys.argv[1:]:
        pid = arg
        if not pid.isdigit():
            r = requests.get(arg, timeout=30, allow_redirects=True, headers={"User-Agent": UA_MOBILE})
            m = re.search(r"(?:item|i)/(\d{8,})\.html", requests.utils.unquote(" ".join([r.url] + [h.headers.get("Location", "") for h in r.history])))
            pid = m.group(1) if m else ""
            print(f"[video] {arg} -> produs {pid or '?'}")
        out[arg] = find_video(pid) if pid else None
    print(json.dumps(out, indent=1))
