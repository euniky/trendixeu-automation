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
import time
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


def accounts_for(*platforms: str) -> dict[str, list[dict]]:
    """Conturile conectate in Zernio, grupate pe platforma (tiktok, instagram, pinterest...)."""
    r = requests.get(f"{API}/accounts", headers=headers(), timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data.get("accounts", data) if isinstance(data, dict) else data
    out: dict[str, list[dict]] = {p: [] for p in platforms}
    for a in items or []:
        pf = str(a.get("platform", "")).lower()
        if pf in out:
            out[pf].append({"id": a.get("_id") or a.get("id") or a.get("accountId"),
                            "username": a.get("username") or a.get("displayName") or a.get("name")})
    return out


def tiktok_accounts() -> list[dict]:
    return accounts_for("tiktok")["tiktok"]


# --- Instagram (Reels) si Pinterest (pin video): acelasi clip, la aceleasi ore ca pe TikTok ---------------
# Se activeaza singure cand contul e conectat in Zernio. EXTRA_PLATFORMS=0 le opreste.
EXTRA_ON = os.environ.get("EXTRA_PLATFORMS", "1") != "0"


def pinterest_board(account: dict, log) -> str | None:
    """Boardul unde ajung pinurile: PINTEREST_BOARD (nume), altfel cel cu 'deal'/'oferte'/'aliexpress', altfel primul."""
    try:
        r = requests.get(f"{API}/accounts/{account['id']}/pinterest-boards", headers=headers(), timeout=30)
        boards = (r.json().get("boards") or []) if r.ok else []
    except Exception as exc:
        log(f"Nu am putut citi boardurile Pinterest: {str(exc)[:120]}")
        return None
    if not boards:
        log("Pinterest: niciun board gasit. Creeaza unul (ex. 'AliExpress Deals') in contul Pinterest.")
        return None
    want = os.environ.get("PINTEREST_BOARD", "").strip().lower()
    pick = next((b for b in boards if want and want == str(b.get("name", "")).lower()), None) \
        or next((b for b in boards if re.search(r"deal|ofert|aliexpress", str(b.get("name", "")), re.I)), None) \
        or boards[0]
    return str(pick.get("id"))


def extra_platform_entries(v: dict, extra: dict, boards: dict) -> list[dict]:
    """Intrarile 'platforms' pentru Instagram / Pinterest, cu linkul clickabil al produsului."""
    clip = v["clip"]
    entries = []
    for a in extra.get("instagram", []):
        entries.append({"platform": "instagram", "accountId": a["id"],
                        "platformSpecificData": {"shareToFeed": True}})
    for a in extra.get("pinterest", []):
        board = boards.get(a["id"])
        if not board:
            continue
        entries.append({"platform": "pinterest", "accountId": a["id"], "platformSpecificData": {
            "boardId": board,
            "title": (clip.get("title_ro") or v.get("title") or "Oferta AliExpress")[:100],
            # Pinterest nu accepta URL-uri scurtate: adresa /go/<nr> e pe domeniul tau si duce la linkul de afiliere
            "link": f"{SITE_URL}/go/{v['num']}"}})
    return entries


def extra_caption(v: dict, platform: str) -> str:
    clip = v["clip"]
    text = clip.get("description_ro", "").replace(f"#{v['num']}", f"nr. {v['num']}")
    text = "\n".join(l for l in text.split("\n") if not l.startswith("🎵")).strip()
    if platform == "instagram":   # in descrierile Instagram linkurile nu se apasa: trimit spre linkul din bio
        tags = " ".join("#" + t for t in clip.get("hashtags", [])[:8])
        return f"{text}\n\n🔗 Produs nr. {v['num']}: link in bio ({SITE_URL.split('//')[1]})\n\n{tags}".strip()[:2150]
    text = "\n".join(l for l in text.split("\n") if "bio" not in l.lower())   # pe Pinterest linkul e chiar pe pin
    return f"{text}\n\n🔗 {SITE_URL}/go/{v['num']}"[:800]


def post_extra(v: dict, when: datetime, url: str, extra: dict, boards: dict, log) -> None:
    """Programeaza clipul si pe Instagram (Pinterest are programul lui, schedule_pinterest). Esecurile nu afecteaza TikTok."""
    entries = [e for e in extra_platform_entries(v, extra, boards) if e["platform"] == "instagram"]
    done = v["posted"].setdefault("extra", {})
    for entry in entries:
        pf = entry["platform"]
        if done.get(pf):
            continue
        body = {"content": extra_caption(v, pf), "scheduledFor": when.isoformat(), "timezone": "Europe/Bucharest",
                "mediaItems": [{"type": "video", "url": url}], "platforms": [entry]}
        try:
            r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=60)
            if r.status_code not in (200, 201, 207) and not r.ok:
                log(f"{pf.capitalize()}: clipul #{v['num']} a esuat: {r.status_code} {r.text[:160]}")
                continue
            data = r.json()
            post = data.get("post", data)
            done[pf] = post.get("_id") or post.get("id") or True
            log(f"Clipul #{v['num']} programat si pe {pf.capitalize()} pentru {when.strftime('%d.%m %H:%M')}")
        except Exception as exc:
            log(f"{pf.capitalize()}: clipul #{v['num']} a esuat: {str(exc)[:160]}")
        time.sleep(1.1)   # Zernio permite 60 cereri/minut


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
    if v.get("remade"):
        url += f"?v={v['remade']}"   # adresa noua pentru clipurile refacute
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
    v["posted"] = {"draft": True, "remade": v.get("remade"), "sent": datetime.now(TZ).isoformat(timespec="minutes"),
                   "id": post.get("_id") or post.get("id"), "account": account["username"]}
    log(f"Clipul #{v['num']} trimis ca SCHITA in inboxul TikTok (pune-i un sunet trending si posteaza-l)")
    return True


# melodii SERIOASE, moderne, care plac publicului larg: fara genuri de petrecere / vacanta / copii
GOOD_GENRES = {"ELECTRONIC", "HIP_HOP/RAP", "R&B/SOUL", "ALTERNATIVE/INDIE", "ROCK", "POP", "EDM"}
BAD_WORDS = re.compile(
    # copii, desene, glume, sarbatori
    r"child|\bkids?\b|\bbaby\b|nursery|rhymes?\b|\btoys?\b|cartoon|anime|kawaii|\bcute\b|funny|silly|\bmemes?\b|"
    r"prank|jingle|birthday|\bparty\b|christmas|xmas|halloween|easter|disney|princess|bunny|chipmunk|"
    r"sped.?up|speed.?up|"
    # trist, horror, somn, liniste
    r"horror|scary|\bsad\b|\bcry\b|heartbreak|blues|lullaby|sleep|\bcalm\b|gentle|\brain\b|"
    # nu sunt melodii de fundal pentru reclame
    r"piano|devotional|prayer|gospel|church|sound effect|\bsound \d|\bbgm\b|\bnews\b|mysterious|"
    # stiluri prea 'vesele' / de vacanta
    r"tropical|reggaeton|summer|beach|holiday|karaoke|nightcore", re.I)


def trending_tracks(account: dict, log) -> list[dict]:
    """Melodiile in trend din biblioteca comerciala TikTok, filtrate pentru reclame de produse."""
    try:
        r = requests.get(f"{API}/accounts/{account['id']}/tiktok/commercial-music", headers=headers(), timeout=30)
        tracks = r.json().get("tracks") or [] if r.ok else []
    except Exception as exc:
        log(f"Nu am putut citi melodiile in trend: {str(exc)[:120]}")
        return []
    good = [t for t in tracks
            if (t.get("durationSec") or 0) >= 30 and set(t.get("genres") or []) & GOOD_GENRES
            and not t.get("explicit")   # fara versuri explicite
            and not BAD_WORDS.search(f"{t.get('name', '')} {t.get('artist', '')} {' '.join(t.get('genres') or [])}")
            and re.search(r"[A-Za-z]", t.get("name", ""))]
    good.sort(key=lambda t: t.get("rank") or 999)
    return good[:15]


def music_settings(track: dict) -> dict:
    return {"musicSoundInfo": {"musicSoundId": str(track["id"]), "musicSoundVolume": 85},
            "videoOriginalSoundVolume": 0}   # muzica de fundal din clip se opreste, ramane doar melodia TikTok


def caption(clip: dict, tiktok_music: bool = False) -> str:
    num = clip.get("num")
    text = clip.get("description_ro", "").replace(f"#{num}", f"nr. {num}")
    if tiktok_music:   # creditul pentru muzica de fundal nu mai e necesar
        text = "\n".join(l for l in text.split("\n") if not l.startswith("🎵"))
    tags = " ".join("#" + t for t in clip.get("hashtags", []))
    return f"{text}\n\n{tags}".strip()[:2150]


# --- Pinterest: un pin la fiecare 30 de minute, cele mai bune produse la orele de varf --------------------
PIN_TIMES = [(h, m) for h in range(10, 24) for m in (0, 30)]          # 10:00 ... 23:30
PIN_PRIME = {(12, 0), (12, 30), (13, 0), (19, 0), (19, 30), (20, 0), (20, 30), (21, 0)}   # pranz si seara
PIN_IMAGES = int(os.environ.get("PIN_IMAGES", "3"))                    # poze diferite pe produs, pe langa clip


def pin_slots(n: int, taken: set[str]) -> list[datetime]:
    """Sloturile libere, in ordinea completarii: azi orele de varf, apoi restul zilei, apoi la fel maine."""
    now = datetime.now(TZ)
    out: list[datetime] = []
    day = now.date()
    while len(out) < n and (day - now.date()).days < 3:
        free = [datetime(day.year, day.month, day.day, h, m, tzinfo=TZ) for h, m in PIN_TIMES]
        free = [t for t in free if t - now >= MIN_LEAD and t.isoformat() not in taken]
        out += [t for t in free if (t.hour, t.minute) in PIN_PRIME] + [t for t in free if (t.hour, t.minute) not in PIN_PRIME]
        day += timedelta(days=1)
    return out[:n]


def schedule_pinterest(video_log: list[dict], extra: dict, boards: dict, off: set, log) -> int:
    """Pinurile Pinterest ale produselor de azi. Ordinea: toate clipurile (cele mai 'wow' primele),
    apoi prima poza a fiecarui produs, apoi a doua etc. — asa produsele bune ajung la orele de varf,
    iar pinurile aceluiasi produs nu vin unul dupa altul."""
    if not extra.get("pinterest"):
        return 0
    acc = extra["pinterest"][0]
    board = boards.get(acc["id"])
    if not board:
        return 0
    today = datetime.utcnow().date().isoformat()
    elig = [v for v in video_log if v.get("clip") and v.get("created") == today and v.get("id") not in off]
    elig.sort(key=lambda v: -(v.get("wow") or 0))
    queue: list[tuple[str, dict, str]] = []   # (cheie, produs, url media)
    urls = {}
    for v in elig:
        urls[v["num"]] = clip_url(v)
    for v in elig:
        if urls[v["num"]]:
            queue.append(("video", v, urls[v["num"]]))
    imgs_of = {}
    for v in elig:
        seen, lst = set(), []
        for u in [v.get("image")] + list(v.get("images") or []):
            if u and str(u).startswith("https://") and u not in seen:
                seen.add(u)
                lst.append(u)
        imgs_of[v["num"]] = lst
    for k in range(PIN_IMAGES):
        for v in elig:
            if len(imgs_of[v["num"]]) > k:
                queue.append((f"img{k + 1}", v, imgs_of[v["num"]][k]))
    queue = [q for q in queue if q[0] not in {p.get("key") for p in q[1].get("pins", [])}]
    if not queue:
        return 0
    taken = {p["when"] for v in video_log for p in v.get("pins", []) if p.get("when")}
    slots = pin_slots(len(queue), taken)
    done = 0
    for (key, v, media), when in zip(queue, slots):
        clip = v["clip"]
        entry = {"platform": "pinterest", "accountId": acc["id"], "platformSpecificData": {
            "boardId": board, "title": (clip.get("title_ro") or v.get("title") or "Oferta AliExpress")[:100],
            "link": f"{SITE_URL}/go/{v['num']}"}}   # linkul clickabil al produsului (nu se accepta URL-uri scurtate)
        body = {"content": extra_caption(v, "pinterest"), "scheduledFor": when.isoformat(), "timezone": "Europe/Bucharest",
                "mediaItems": [{"type": "video" if key == "video" else "image", "url": media}], "platforms": [entry]}
        try:
            r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=60)
            if not r.ok:
                log(f"Pinterest: {key} pentru #{v['num']} a esuat: {r.status_code} {r.text[:160]}")
                continue
            data = r.json()
            post = data.get("post", data)
            v.setdefault("pins", []).append({"key": key, "when": when.isoformat(), "id": post.get("_id") or post.get("id")})
            done += 1
            log(f"Pinterest {when.strftime('%d.%m %H:%M')}: #{v['num']} ({key})")
        except Exception as exc:
            log(f"Pinterest: {key} pentru #{v['num']} a esuat: {str(exc)[:160]}")
        time.sleep(1.1)   # Zernio permite 60 cereri/minut
    return done


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
    extra: dict[str, list[dict]] = {"instagram": [], "pinterest": []}
    try:
        grouped = accounts_for("tiktok", "instagram", "pinterest")
        accounts = grouped["tiktok"]
        if EXTRA_ON:
            extra = {k: grouped[k] for k in extra}
    except Exception as exc:
        log(f"Nu m-am putut conecta la Zernio: {str(exc)[:200]}")
        accounts = []
    report["accounts"] = accounts
    report["extra_accounts"] = extra
    for pf, lst in extra.items():
        log(f"Conturi {pf.capitalize()} conectate: " + (", ".join(str(a["username"]) for a in lst) or "niciunul"))
    if not accounts:
        log("Niciun cont TikTok conectat in Zernio.")
    else:
        report["ok"] = True
        log("Conturi TikTok conectate: " + ", ".join(f"@{a['username']}" for a in accounts))

    if check and accounts:
        try:   # sunetele din biblioteca comerciala TikTok (merg doar pe conturi conectate prin TikTok Business)
            r = requests.get(f"{API}/accounts/{accounts[0]['id']}/tiktok/commercial-music", headers=headers(), timeout=30)
            report["cml_status"] = r.status_code
            report["cml_tracks"] = [{k: t.get(k) for k in ("id", "name", "artist", "durationSec", "genres", "rank")}
                                    for t in (r.json().get("tracks") or [])] if r.ok else []
            log(f"Biblioteca comerciala TikTok: {r.status_code} {r.text[:200]}")
        except Exception as exc:
            log(f"Biblioteca comerciala TikTok: eroare {str(exc)[:150]}")

        # starea clipurilor deja programate (publicat / in asteptare / esuat)
        vlog = json.loads(VIDEO_LOG.read_text(encoding="utf-8")) if VIDEO_LOG.exists() else []
        report["posts"] = []
        recent = (datetime.now(TZ) - timedelta(days=3)).date().isoformat()
        for v in vlog:
            pid = (v.get("posted") or {}).get("id")
            if not pid or v.get("created", "") < recent:   # doar ultimele zile (Zernio permite 60 cereri/minut)
                continue
            time.sleep(1.1)
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

    # produsele ascunse sau sterse din consola nu se posteaza; programarile lor viitoare se anuleaza
    off = set()
    try:
        m = requests.get(f"{SITE_URL}/.netlify/functions/moderate", timeout=20).json()
        off = set(m.get("hidden") or []) | set(m.get("deleted") or [])
    except Exception as exc:
        log(f"Nu am putut citi produsele ascunse din consola: {str(exc)[:120]}")
    now = datetime.now(TZ)
    for v in video_log:
        po = v.get("posted") or {}
        if v.get("id") in off and po.get("scheduledFor") and po.get("id") and not po.get("cancelled") \
                and datetime.fromisoformat(po["scheduledFor"]) - now > timedelta(minutes=5):
            r = requests.delete(f"{API}/posts/{po['id']}", headers=headers(), timeout=30)
            if r.ok or r.status_code == 404:
                po["cancelled"] = True
                log(f"Clipul #{v['num']} a fost ascuns din consola: programarea de la {po['scheduledFor'][11:16]} e anulata")

    # mutare manuala in schite (TO_DRAFTS="13,14"): anulez programarea si trimit clipul in inbox
    move = {int(x) for x in re.findall(r"\d+", os.environ.get("TO_DRAFTS", ""))}
    for v in video_log:
        po = v.get("posted") or {}
        if v.get("num") not in move or (po.get("draft") and po.get("remade") == v.get("remade")):
            continue   # schita e deja trimisa pentru varianta asta a clipului
        if po.get("id") and not po.get("draft"):
            r = requests.delete(f"{API}/posts/{po['id']}", headers=headers(), timeout=30)
            if not r.ok and r.status_code != 404:
                log(f"Nu am putut anula programarea clipului #{v['num']}: {r.status_code} {r.text[:150]}")
                continue
            log(f"Programarea clipului #{v['num']} a fost anulata")
        if send_draft(v, account, log):
            report["drafts"].append({"num": v["num"], "title": v["clip"].get("title_ro")})

    # repostare ceruta in tema zilei (clip refacut, programat din nou)
    try:
        theme = json.loads((ROOT / "data" / "theme.json").read_text(encoding="utf-8"))
    except Exception:
        theme = {}
    if theme.get("date") == datetime.utcnow().date().isoformat():
        for v in video_log:
            po = v.get("posted") or {}
            if v.get("num") in theme.get("repost", []) and not po.get("repost_day") == theme["date"]:
                v["posted_before"] = (v.get("posted_before") or []) + ([po] if po else [])
                v["posted"] = None
                v["created"] = datetime.utcnow().date().isoformat()
                v["_repost_day"] = theme["date"]
                log(f"Clipul #{v['num']} va fi repostat azi (refacut)")

    if os.environ.get("RESCHEDULE_MUSIC") == "1":
        for v in video_log:
            po = v.get("posted") or {}
            if po.get("scheduledFor") and po.get("id") and not po.get("music") and not po.get("cancelled") \
                    and datetime.fromisoformat(po["scheduledFor"]) - now > timedelta(minutes=30):
                r = requests.delete(f"{API}/posts/{po['id']}", headers=headers(), timeout=30)
                if r.ok or r.status_code == 404:
                    v["posted"] = None
                    v["_slot"] = po["scheduledFor"]
                    v["created"] = datetime.utcnow().date().isoformat()
                    log(f"Clipul #{v['num']} reprogramat la {po['scheduledFor'][11:16]} cu o melodie in trend")

    # doar clipurile generate azi (cele mai vechi le-ai postat poate deja manual)
    today = datetime.utcnow().date().isoformat()
    todo = [v for v in video_log if v.get("clip") and not v.get("posted") and v.get("created") == today
            and v.get("id") not in off]
    todo.sort(key=lambda v: -(v.get("wow") or 0))
    drafts_today = sum(1 for v in video_log if (v.get("posted") or {}).get("draft")
                       and (v.get("posted") or {}).get("sent", "")[:10] == datetime.now(TZ).date().isoformat())
    n_drafts = max(0, DRAFTS_PER_DAY - drafts_today)
    for v in [x for x in todo if not x.get("_slot") and not x.get("_repost_day")][:n_drafts]:
        if send_draft(v, account, log):
            report["drafts"].append({"num": v["num"], "title": v["clip"].get("title_ro")})
    todo = [v for v in todo if not v.get("posted")]

    boards = {a["id"]: pinterest_board(a, log) for a in extra["pinterest"]}
    tracks = trending_tracks(account, log)
    if tracks:
        log("Melodii in trend disponibile: " + ", ".join(f"{t['name']} ({t['artist']})" for t in tracks[:6]))
    taken = {v["posted"]["scheduledFor"] for v in video_log
             if (v.get("posted") or {}).get("scheduledFor") and not v["posted"].get("cancelled")}
    fixed = [v for v in todo if v.get("_slot")]
    rest = [v for v in todo if not v.get("_slot")]
    slots = next_slots(len(rest), taken | {v["_slot"] for v in fixed})
    pairs = [(v, datetime.fromisoformat(v.pop("_slot"))) for v in fixed] + list(zip(rest, slots))
    for v, when in pairs:
        clip = v["clip"]
        url = clip_url(v)
        if not url:
            log(f"Clipul #{v['num']} nu e accesibil online; il las pentru data viitoare.")
            continue
        track = tracks[(v["num"] + when.hour) % len(tracks)] if tracks else None
        body = {
            "content": caption(clip, tiktok_music=bool(track)),
            "scheduledFor": when.isoformat(),
            "timezone": "Europe/Bucharest",
            "mediaItems": [{"type": "video", "url": url}],
            "platforms": [{"platform": "tiktok", "accountId": account["id"]}],
            "tiktokSettings": {**settings(), **(music_settings(track) if track else {})},
        }
        try:
            r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=60)
            if not r.ok and track:   # daca TikTok refuza melodia, postez cu muzica din clip
                log(f"Melodia TikTok refuzata pentru #{v['num']} ({r.status_code} {r.text[:120]}), postez fara ea")
                track = None
                body["content"] = caption(clip)
                body["tiktokSettings"] = settings()
                r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=60)
            if not r.ok:
                log(f"Programarea clipului #{v['num']} a esuat: {r.status_code} {r.text[:200]}")
                continue
            data = r.json()
            post = data.get("post", data)
            v["posted"] = {"scheduledFor": when.isoformat(), "id": post.get("_id") or post.get("id"),
                           "account": account["username"],
                           "music": f"{track['name']} – {track['artist']}" if track else None,
                           "repost_day": v.pop("_repost_day", None)}
            report["scheduled"].append({"num": v["num"], "when": when.isoformat(), "title": clip.get("title_ro")})
            if EXTRA_ON and extra["instagram"]:
                post_extra(v, when, url, extra, boards, log)
            log(f"Clipul #{v['num']} programat pe TikTok pentru {when.strftime('%d.%m %H:%M')}"
                + (f", cu melodia {track['name']} ({track['artist']})" if track else ""))
        except Exception as exc:
            log(f"Programarea clipului #{v['num']} a esuat: {str(exc)[:200]}")
    if EXTRA_ON and extra["pinterest"]:
        n_pins = schedule_pinterest(video_log, extra, boards, off, log)
        report["pinterest_pins"] = n_pins
    if not todo:
        log("Niciun clip nou de programat.")
    VIDEO_LOG.write_text(json.dumps(video_log, ensure_ascii=False, indent=1), encoding="utf-8")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
