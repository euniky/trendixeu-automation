"""
Posteaza automat pe TikTok, prin Zernio, clipurile noi din ziua curenta,
programate la orele cand publicul din Romania e cel mai activ.

Ruleaza in GitHub Actions dupa publicarea site-ului (clipurile trebuie sa fie online).
Are nevoie de secretul ZERNIO_API_KEY. Fara el nu face nimic.
  python scripts/post_tiktok.py          -> programeaza clipurile noi
  python scripts/post_tiktok.py --check  -> doar verifica conexiunea si conturile
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
VIDEO_LOG = ROOT / "data" / "video_log.json"
REPORT = ROOT / "public" / "zernio.json"
API = "https://zernio.com/api/v1"
SITE_URL = "https://trendixeu.netlify.app"
TZ = ZoneInfo("Europe/Bucharest")
# clipurile postate automat (cu muzica de fundal); 13:00 si 20:00 raman libere pentru schitele tale
SLOTS = [(12, 0), (14, 0), (19, 0), (21, 0)]
# cele mai "hot" clipuri ale zilei ajung ca schita in inboxul TikTok: le pui tu un sunet trending si le postezi
DRAFTS_PER_DAY = int(os.environ.get("DRAFTS_PER_DAY", "2"))
MIN_LEAD = timedelta(minutes=20)          # nu programa mai devreme de atat


def headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['ZERNIO_API_KEY'].strip()}", "Content-Type": "application/json"}


def tiktok_accounts() -> list[dict]:
    r = requests.get(f"{API}/accounts", headers=headers(), timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data.get("accounts", data) if isinstance(data, dict) else data
    out = []
    for a in items or []:
        if str(a.get("platform", "")).lower() == "tiktok":
            out.append({"id": a.get("_id") or a.get("id") or a.get("accountId"),
                        "username": a.get("username") or a.get("displayName") or a.get("name")})
    return out


def next_slots(n: int, taken: set[str]) -> list[datetime]:
    now = datetime.now(TZ)
    out, day = [], now.date()
    while len(out) < n:
        for h, m in SLOTS:
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)
            if t - now >= MIN_LEAD and t.isoformat() not in taken:
                out.append(t)
                if len(out) == n:
                    break
        day += timedelta(days=1)
    return out


def settings(draft: bool = False) -> dict:
    st = {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True, "allow_duet": True, "allow_stitch": True,
          "content_preview_confirmed": True, "express_consent_given": True,
          # promovezi produse cu link de afiliere: TikTok cere marcarea continutului comercial
          "isBrandOrganicPost": True}
    if draft:
        st["draft"] = True
    return st


def clip_url(v: dict) -> str | None:
    url = f"{os.environ.get('CLIP_BASE', SITE_URL).rstrip('/')}/{v['clip']['file']}"
    head = requests.head(url, timeout=30, allow_redirects=True)
    if not head.ok or "video" not in head.headers.get("Content-Type", ""):
        return None
    return url


def send_draft(v: dict, account: dict, log) -> bool:
    url = clip_url(v)
    if not url:
        log(f"Clipul #{v['num']} nu e accesibil online; schita ramane pentru data viitoare.")
        return False
    body = {"content": caption(v["clip"]) + "\n", "publishNow": True,
            "mediaItems": [{"type": "video", "url": url}],
            "platforms": [{"platform": "tiktok", "accountId": account["id"]}],
            "tiktokSettings": settings(draft=True)}
    r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=180)
    if not r.ok:
        log(f"Schita clipului #{v['num']} a esuat: {r.status_code} {r.text[:200]}")
        return False
    post = r.json().get("post", r.json())
    v["posted"] = {"draft": True, "sent": datetime.now(TZ).isoformat(timespec="minutes"),
                   "id": post.get("_id") or post.get("id"), "account": account["username"]}
    log(f"Clipul #{v['num']} trimis ca SCHITA in inboxul TikTok (pune-i un sunet trending si posteaza-l)")
    return True


def caption(clip: dict) -> str:
    num = clip.get("num")
    text = clip.get("description_ro", "").replace(f"#{num}", f"nr. {num}")
    tags = " ".join("#" + t for t in clip.get("hashtags", []))
    return f"{text}\n\n{tags}".strip()[:2150]


def main() -> None:
    check = "--check" in sys.argv
    report: dict = {"checked": datetime.now(TZ).isoformat(timespec="seconds"), "ok": False, "scheduled": [], "log": []}

    def log(msg: str) -> None:
        print(msg)
        report["log"].append(msg)

    if not os.environ.get("ZERNIO_API_KEY", "").strip():
        log("Zernio nu e configurat (lipseste secretul ZERNIO_API_KEY): clipurile nu se posteaza automat.")
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    try:
        accounts = tiktok_accounts()
    except Exception as exc:
        log(f"Nu m-am putut conecta la Zernio: {str(exc)[:200]}")
        accounts = []
    report["accounts"] = accounts
    if not accounts:
        log("Niciun cont TikTok conectat in Zernio.")
    else:
        report["ok"] = True
        log("Conturi TikTok conectate: " + ", ".join(f"@{a['username']}" for a in accounts))

    if check and accounts:
        # starea clipurilor deja programate (publicat / in asteptare / esuat)
        vlog = json.loads(VIDEO_LOG.read_text(encoding="utf-8")) if VIDEO_LOG.exists() else []
        report["posts"] = []
        for v in vlog:
            pid = (v.get("posted") or {}).get("id")
            if not pid:
                continue
            try:
                r = requests.get(f"{API}/posts/{pid}", headers=headers(), timeout=30)
                data = r.json() if r.ok else {"error": f"{r.status_code} {r.text[:300]}"}
            except Exception as exc:
                data = {"error": str(exc)[:200]}
            post = data.get("post", data) if isinstance(data, dict) else data
            report["posts"].append({"num": v["num"], "when": v["posted"].get("scheduledFor"), "raw": post})
            st = post.get("status") if isinstance(post, dict) else None
            when = v["posted"].get("scheduledFor", "")[11:16] or "schita"
            log(f"Clipul #{v['num']} ({when}): {st or post}")

    if check or not accounts:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return

    account = accounts[0]
    video_log = json.loads(VIDEO_LOG.read_text(encoding="utf-8")) if VIDEO_LOG.exists() else []
    report["drafts"] = []

    # mutare manuala in schite (TO_DRAFTS="13,14"): anulez programarea si trimit clipul in inbox
    move = {int(x) for x in re.findall(r"\d+", os.environ.get("TO_DRAFTS", ""))}
    for v in video_log:
        po = v.get("posted") or {}
        if v.get("num") not in move or po.get("draft"):
            continue
        if po.get("id"):
            r = requests.delete(f"{API}/posts/{po['id']}", headers=headers(), timeout=30)
            if not r.ok and r.status_code != 404:
                log(f"Nu am putut anula programarea clipului #{v['num']}: {r.status_code} {r.text[:150]}")
                continue
            log(f"Programarea clipului #{v['num']} a fost anulata")
        if send_draft(v, account, log):
            report["drafts"].append({"num": v["num"], "title": v["clip"].get("title_ro")})

    # doar clipurile generate azi (cele mai vechi le-ai postat poate deja manual)
    today = datetime.utcnow().date().isoformat()
    todo = [v for v in video_log if v.get("clip") and not v.get("posted") and v.get("created") == today]
    todo.sort(key=lambda v: -(v.get("wow") or 0))
    drafts_today = sum(1 for v in video_log if (v.get("posted") or {}).get("draft")
                       and (v.get("posted") or {}).get("sent", "")[:10] == datetime.now(TZ).date().isoformat())
    n_drafts = max(0, DRAFTS_PER_DAY - drafts_today)
    for v in todo[:n_drafts]:
        if send_draft(v, account, log):
            report["drafts"].append({"num": v["num"], "title": v["clip"].get("title_ro")})
    todo = [v for v in todo if not v.get("posted")]

    taken = {v["posted"]["scheduledFor"] for v in video_log if (v.get("posted") or {}).get("scheduledFor")}
    slots = next_slots(len(todo), taken)
    for v, when in zip(todo, slots):
        clip = v["clip"]
        url = clip_url(v)
        if not url:
            log(f"Clipul #{v['num']} nu e accesibil online; il las pentru data viitoare.")
            continue
        body = {
            "content": caption(clip),
            "scheduledFor": when.isoformat(),
            "timezone": "Europe/Bucharest",
            "mediaItems": [{"type": "video", "url": url}],
            "platforms": [{"platform": "tiktok", "accountId": account["id"]}],
            "tiktokSettings": settings(),
        }
        try:
            r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=60)
            if not r.ok:
                log(f"Programarea clipului #{v['num']} a esuat: {r.status_code} {r.text[:200]}")
                continue
            data = r.json()
            post = data.get("post", data)
            v["posted"] = {"scheduledFor": when.isoformat(), "id": post.get("_id") or post.get("id"),
                           "account": account["username"]}
            report["scheduled"].append({"num": v["num"], "when": when.isoformat(), "title": clip.get("title_ro")})
            log(f"Clipul #{v['num']} programat pe TikTok pentru {when.strftime('%d.%m %H:%M')}")
        except Exception as exc:
            log(f"Programarea clipului #{v['num']} a esuat: {str(exc)[:200]}")
    if not todo:
        log("Niciun clip nou de programat.")
    VIDEO_LOG.write_text(json.dumps(video_log, ensure_ascii=False, indent=1), encoding="utf-8")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
