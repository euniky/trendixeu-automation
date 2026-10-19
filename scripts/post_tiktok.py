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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pin_seo  # noqa: E402  titluri si descrieri optimizate pentru cautarea Pinterest

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


BOARD_LISTS: dict[str, list[dict]] = {}   # toate boardurile fiecarui cont Pinterest


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
    BOARD_LISTS[account["id"]] = boards
    want = os.environ.get("PINTEREST_BOARD", "").strip().lower()
    pick = next((b for b in boards if want and want == str(b.get("name", "")).lower()), None) \
        or next((b for b in boards if re.search(r"deal|ofert|aliexpress", str(b.get("name", "")), re.I)), None) \
        or boards[0]
    return str(pick.get("id"))


# Boardul tematic pentru fiecare categorie de produs: se cauta un board al carui nume contine unul dintre cuvinte.
# Daca nu exista board potrivit, pinul ajunge in boardul general (AliExpress Deals).
BOARD_THEMES = [
    (("christmas", "xmas", "craciun"), ("craciun", "christmas")),
    (("cleaning", "carpet", "vacuum", "mop", "steam", "window"), ("curat", "clean")),
    (("kitchen",), ("bucat", "kitchen")),
    (("home gadget", "home"), ("casa", "casă", "home")),
    (("car ", "car", "dash cam", "motorcycle", "moto", "bike"), ("auto", "moto", "masina", "mașina", "car")),
    (("pet",), ("animal", "pet", "câin", "caine", "pisic")),
    (("phone",), ("telefon", "phone", "gadget")),
    (("beauty",), ("frumus", "beauty", "îngrijire", "ingrijire")),
    (("slingshot", "outdoor", "camping", "fishing", "hobby"), ("hobby", "outdoor", "sport")),
    (("tool", "drill", "diy", "bricolaj", "screwdriver"), ("bricolaj", "diy", "scule", "unelte")),
]


def _plain(t: str) -> str:
    """Fara diacritice, litere mici: 'Curățenie' -> 'curatenie'."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(t).lower()) if not unicodedata.combining(c))


def board_for(v: dict, acc_boards: list[dict], default: str | None) -> str | None:
    kw = f" {(v.get('kw') or '').lower()} {(v.get('title') or '').lower()} "
    for keys, names in BOARD_THEMES:
        if any(k in kw for k in keys):
            for b in acc_boards:
                if any(_plain(n) in _plain(b.get("name", "")) for n in names):
                    return str(b.get("id"))
            return default   # categoria e cunoscuta, dar nu are board: boardul general
    return default


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
            "link": pin_link(v)}})
    return entries


def pin_link(v: dict) -> str:
    """Linkul pinului. Pinterest a respins adresele de pe netlify.app (pagini-intermediar), asa ca implicit
    linkul de afiliere merge direct. PIN_LINK_MODE=go foloseste adresa /go/<nr> (de ex. cu domeniu propriu)."""
    if os.environ.get("PIN_LINK_MODE", "direct") == "go":
        return f"{SITE_URL}/go/{v['num']}"
    return v.get("link") or f"{SITE_URL}/go/{v['num']}"


def extra_caption(v: dict, platform: str) -> str:
    clip = v["clip"]
    text = clip.get("description_ro", "").replace(f"#{v['num']}", f"nr. {v['num']}")
    text = "\n".join(l for l in text.split("\n") if not l.startswith("🎵")).strip()
    if platform == "instagram":   # in descrierile Instagram linkurile nu se apasa: trimit spre linkul din bio
        tags = " ".join("#" + t for t in clip.get("hashtags", [])[:8])
        return f"{text}\n\n🔗 Produs nr. {v['num']}: link in bio ({SITE_URL.split('//')[1]})\n\n{tags}".strip()[:2150]
    text = "\n".join(l for l in text.split("\n") if "bio" not in l.lower())   # pe Pinterest linkul e chiar pe pin
    return f"{text}\n\n🔗 Link afiliat / Affiliate link"[:800]   # Pinterest cere mentionarea linkurilor de afiliere


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
GOOD_GENRES = {"ELECTRONIC", "HIP_HOP/RAP", "R&B/SOUL", "ALTERNATIVE/INDIE", "POP", "EDM", "DANCE", "HOUSE"}
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
    r"tropical|reggaeton|summer|beach|holiday|karaoke|nightcore|"
    # orchestral / simfonic / epic si doar tobe
    r"orchestr|symphon|classical|epic|cinematic|trailer|soundtrack|heroic|battle|\bwar\b|march|"
    r"drum|percussion|tribal|taiko|marching", re.I)


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
            and not set(t.get("genres") or []) & {"CLASSICAL", "SOUNDTRACK", "CHILDREN", "ROCK", "METAL"}
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
# Cont nou: putine pinuri, toate cu produse DIFERITE (Pinterest penalizeaza repetarea aceluiasi link/aceleiasi poze).
# Un pin video pe produs, raspandite peste zi; cele mai "wow" la orele de varf.
PIN_TIMES = [(10, 30), (12, 0), (14, 0), (16, 30), (19, 0), (20, 30), (21, 30)]
PIN_PRIME = {(12, 0), (19, 0), (20, 30)}                              # pranz si seara
PIN_IMAGES = int(os.environ.get("PIN_IMAGES", "0"))                    # poze in plus pe produs (0 = doar clipul)
# limita Zernio e 25/zi; noi tinem ~9/zi: 6 video + 3 de Craciun (christmas_pins.py), plus loc pentru pinuri manuale
PIN_DAILY_MAX = int(os.environ.get("PIN_DAILY_MAX", "6"))


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
    today_local = datetime.now(TZ).date().isoformat()
    used = sum(1 for v in video_log for p in v.get("pins", []) if (p.get("when") or "")[:10] == today_local)
    queue = queue[:max(0, PIN_DAILY_MAX - used)]
    slots = pin_slots(len(queue), taken)
    done = 0
    for (key, v, media), when in zip(queue, slots):
        clip = v["clip"]
        if key == "video":   # fara muzica: Pinterest sterge sunetul cu drepturi inregistrate
            media = muted_video(media, log)
            if not media:
                log(f"Pinterest: #{v['num']} sarit (nu am putut face copia fara sunet)")
                continue
        entry = {"platform": "pinterest", "accountId": acc["id"], "platformSpecificData": {
            "boardId": board_for(v, BOARD_LISTS.get(acc["id"], []), board), "title": pin_seo.pin_title(v),
            "link": pin_link(v)}}   # linkul clickabil al produsului
        if key == "video":   # coperta clara a produsului (altfel Pinterest ia primul cadru, in ceata)
            cover = make_pin_cover(v, log)
            if cover:
                entry["platformSpecificData"]["coverImageUrl"] = cover
            else:
                entry["platformSpecificData"]["coverImageKeyFrameTime"] = 2.0
        body = {"content": pin_seo.pin_description(v), "scheduledFor": when.isoformat(), "timezone": "Europe/Bucharest",
                "mediaItems": [{"type": "video" if key == "video" else "image", "url": media}], "platforms": [entry]}
        try:
            r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=60)
            if not r.ok:
                log(f"Pinterest: {key} pentru #{v['num']} a esuat: {r.status_code} {r.text[:600]}")
                continue
            data = r.json()
            post = data.get("post", data)
            v.setdefault("pins", []).append({"key": key, "when": when.isoformat(), "id": post.get("_id") or post.get("id")})
            done += 1
            bname = next((b.get("name") for b in BOARD_LISTS.get(acc["id"], [])
                          if str(b.get("id")) == entry["platformSpecificData"]["boardId"]), "?")
            log(f"Pinterest {when.strftime('%d.%m %H:%M')}: #{v['num']} ({key}) -> board {bname}")
        except Exception as exc:
            log(f"Pinterest: {key} pentru #{v['num']} a esuat: {str(exc)[:160]}")
        time.sleep(1.1)   # Zernio permite 60 cereri/minut
    return done


def pin_now(num: int, extra: dict, boards: dict, video_log: list[dict], log) -> None:
    """Test: publica ACUM un pin cu poza produsului `num` si raporteaza raspunsul Pinterest (PIN_NOW=<nr>)."""
    v = next((x for x in video_log if x.get("num") == num and x.get("clip")), None)
    if not v or not extra.get("pinterest"):
        log(f"Test pin: produsul #{num} sau contul Pinterest lipseste.")
        return
    acc = extra["pinterest"][0]
    board = boards.get(acc["id"])
    img = next((i for i in [v.get("image")] + list(v.get("images") or []) if i), None)
    entry = {"platform": "pinterest", "accountId": acc["id"], "platformSpecificData": {
        "boardId": board, "title": (v["clip"].get("title_ro") or "Oferta AliExpress")[:100], "link": pin_link(v)}}
    body = {"content": extra_caption(v, "pinterest"), "publishNow": True,
            "mediaItems": [{"type": "image", "url": img}], "platforms": [entry]}
    r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=120)
    log(f"Test pin #{num} (link {pin_link(v)[:60]}...): HTTP {r.status_code} {r.text[:900]}")
    try:
        post = r.json().get("post", r.json())
        pid = post.get("_id") or post.get("id")
        if pid:
            time.sleep(8)
            g = requests.get(f"{API}/posts/{pid}", headers=headers(), timeout=30)
            log(f"Test pin, stare dupa 8 s: {g.text[:900]}")
    except Exception as exc:
        log(f"Test pin: nu am putut citi starea: {str(exc)[:200]}")


FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def make_collage(urls: list[str], title: str, price: str, log) -> str | None:
    """Pinterest nu accepta pinuri cu mai multe poze prin API, asa ca fac un COLAJ vertical 2:3 (1000x1500):
    poza principala sus, banda cu denumirea si pretul, apoi inca 3 poze ale produsului. Il incarc in Zernio."""
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    imgs = []
    for u in urls:
        if len(imgs) == 4:
            break
        try:
            r = requests.get(u, timeout=40, headers={"User-Agent": "Mozilla/5.0"})
            im = Image.open(BytesIO(r.content)).convert("RGB")
            if min(im.size) >= 300:
                imgs.append(im)
        except Exception:
            continue
    if len(imgs) < 2:
        return None
    W, H, TOP, BAND = 1000, 1500, 1000, 167
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(ImageOps.fit(imgs[0], (W, TOP), Image.LANCZOS), (0, 0))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, TOP, W, TOP + BAND], fill=(20, 20, 26))
    try:
        fb = ImageFont.truetype(str(FONT_DIR / "Poppins-Bold.ttf"), 40)
        fp = ImageFont.truetype(str(FONT_DIR / "Poppins-Bold.ttf"), 46)
    except Exception:
        fb = fp = ImageFont.load_default()
    name = title.split("|")[0].strip()
    while d.textlength(name, font=fb) > W - 300 and len(name) > 10:
        name = name.rsplit(" ", 1)[0]
    d.text((36, TOP + BAND / 2), name, font=fb, fill=(255, 255, 255), anchor="lm")
    if price:
        d.text((W - 36, TOP + BAND / 2), price, font=fp, fill=(255, 213, 74), anchor="rm")
    small = imgs[1:4]
    sw = W // max(len(small), 1)
    for i, im in enumerate(small):
        canvas.paste(ImageOps.fit(im, (sw, H - TOP - BAND), Image.LANCZOS), (i * sw, TOP + BAND))
    for i in range(1, len(small)):   # linii subtiri intre poze
        d.line([(i * sw, TOP + BAND), (i * sw, H)], fill=(255, 255, 255), width=4)
    buf = BytesIO()
    canvas.save(buf, "JPEG", quality=90, optimize=True)
    try:
        pre = requests.post(f"{API}/media/presign", headers=headers(), timeout=30,
                            json={"filename": "pin.jpg", "contentType": "image/jpeg", "size": buf.tell()})
        pre.raise_for_status()
        dd = pre.json()
        up = requests.put(dd["uploadUrl"], data=buf.getvalue(), headers={"Content-Type": "image/jpeg"}, timeout=120)
        up.raise_for_status()
        return dd["publicUrl"]
    except Exception as exc:
        log(f"Colaj: incarcarea a esuat: {str(exc)[:150]}")
        return None


def make_pin_cover(v: dict, log) -> str | None:
    """Coperta pinului video: poza produsului CLARA si intreaga (1080x1920), fara estompare, pe fundal inchis,
    cu pretul. Fara ea Pinterest ia primul cadru al clipului (efect de zoom/tremur, arata ca in ceata)."""
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    src = v.get("image") or next(iter(v.get("images") or []), None)
    if not src:
        return None
    try:
        r = requests.get(src, timeout=40, headers={"User-Agent": "Mozilla/5.0"})
        im = ImageOps.exif_transpose(Image.open(BytesIO(r.content))).convert("RGB")
    except Exception as exc:
        log(f"Coperta Pinterest: poza nu s-a putut descarca: {str(exc)[:120]}")
        return None
    if min(im.size) < 300:
        return None
    W, H = 1080, 1920
    canvas = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(canvas)
    for y in range(H):   # degrade vertical inchis (fara blur)
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(22 + 24 * t), int(20 + 10 * t), int(34 + 30 * t)))
    box = 1000
    k = box / max(im.size)
    big = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    card = Image.new("RGB", (box + 40, big.height + 40), (255, 255, 255))
    card.paste(big, (20, 20))
    cx, cy = (W - card.width) // 2, 330 + (box + 40 - card.height) // 2
    canvas.paste(card, (cx, cy))
    try:
        fb = ImageFont.truetype(str(FONT_DIR / "Poppins-Bold.ttf"), 54)
        fs = ImageFont.truetype(str(FONT_DIR / "Poppins-Bold.ttf"), 64)
    except Exception:
        fb = fs = ImageFont.load_default()
    clip = v.get("clip") or {}
    name = (clip.get("title_ro") or clip.get("name") or "").split("|")[0].strip()
    while name and d.textlength(name, font=fb) > W - 120 and len(name) > 10:
        name = name.rsplit(" ", 1)[0]
    y0 = cy + card.height + 70
    if name:
        d.text((W // 2, y0), name, font=fb, fill=(255, 255, 255), anchor="mm")
    price = clip.get("price_text")
    if price:
        w = d.textlength(price, font=fs) + 80
        d.rounded_rectangle([W / 2 - w / 2, y0 + 70, W / 2 + w / 2, y0 + 170], radius=50, fill=(255, 213, 74))
        d.text((W // 2, y0 + 120), price, font=fs, fill=(20, 20, 26), anchor="mm")
    buf = BytesIO()
    canvas.save(buf, "JPEG", quality=92, optimize=True)
    try:
        pre = requests.post(f"{API}/media/presign", headers=headers(), timeout=30,
                            json={"filename": "cover.jpg", "contentType": "image/jpeg", "size": buf.tell()})
        pre.raise_for_status()
        dd = pre.json()
        up = requests.put(dd["uploadUrl"], data=buf.getvalue(), headers={"Content-Type": "image/jpeg"}, timeout=120)
        up.raise_for_status()
        return dd["publicUrl"]
    except Exception as exc:
        log(f"Coperta Pinterest: incarcarea a esuat: {str(exc)[:150]}")
        return None


def muted_video(url: str, log) -> str | None:
    """Copie FARA SUNET a clipului, incarcata in Zernio. Pe Pinterest muzica din clipuri (Kevin MacLeod) e detectata
    ca avand drepturi inregistrate (Frequency Music) si sunetul e sters; repetat, asta poate afecta contul.
    Pinterest reda video-urile fara sunet in feed oricum."""
    import subprocess
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    src, out = tmp / "src.mp4", tmp / "mute.mp4"
    try:
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        src.write_bytes(r.content)
        res = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-an", "-c:v", "copy",
                              "-movflags", "+faststart", str(out)], capture_output=True, text=True, timeout=300)
        if res.returncode or not out.exists():
            log(f"Video fara sunet: ffmpeg a esuat: {res.stderr[:200]}")
            return None
        pre = requests.post(f"{API}/media/presign", headers=headers(), timeout=30,
                            json={"filename": "pin.mp4", "contentType": "video/mp4", "size": out.stat().st_size})
        pre.raise_for_status()
        d = pre.json()
        up = requests.put(d["uploadUrl"], data=out.read_bytes(), headers={"Content-Type": "video/mp4"}, timeout=600)
        up.raise_for_status()
        return d["publicUrl"]
    except Exception as exc:
        log(f"Video fara sunet: {str(exc)[:200]}")
        return None


def prepare_video(src_url: str, log) -> str | None:
    """Descarca filmarea vanzatorului, o face verticala 1080x1920 (fundal estompat), mai clara si mai vie
    (contrast, culori, claritate), H.264 de calitate inalta, apoi o incarca in Zernio. Intoarce URL-ul public."""
    import subprocess
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    src, out = tmp / "src.mp4", tmp / "pin.mp4"
    r = requests.get(src_url, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
    if not r.ok or len(r.content) < 50_000:
        log(f"Video: nu am putut descarca filmarea ({r.status_code})")
        return None
    src.write_bytes(r.content)
    vf = ("split[a][b];"
          "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:2,eq=brightness=-0.08[bg];"
          "[b]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,"
          "eq=contrast=1.08:saturation=1.18:gamma=0.97,unsharp=5:5:0.8:3:3:0.3[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p")
    # fisier mic (~4–5 Mbit/s): Pinterest renunta la procesare dupa 60 s la video-urile prea mari
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", "60", "-filter_complex", vf, "-c:v", "libx264",
           "-preset", "medium", "-crf", "22", "-maxrate", "5M", "-bufsize", "10M", "-profile:v", "high",
           "-an", "-movflags", "+faststart", str(out)]   # fara sunet (drepturi muzica)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode or not out.exists():
        log(f"Video: conversia a esuat: {res.stderr[:300]}")
        return None
    pre = requests.post(f"{API}/media/presign", headers=headers(), timeout=30,
                        json={"filename": "pin.mp4", "contentType": "video/mp4", "size": out.stat().st_size})
    if not pre.ok:
        log(f"Video: presign a esuat: {pre.status_code} {pre.text[:200]}")
        return None
    d = pre.json()
    up = requests.put(d["uploadUrl"], data=out.read_bytes(), headers={"Content-Type": "video/mp4"}, timeout=600)
    if not up.ok:
        log(f"Video: incarcarea a esuat: {up.status_code} {up.text[:200]}")
        return None
    log(f"Video pregatit: 1080x1920, {out.stat().st_size / 1e6:.1f} MB")
    return d["publicUrl"]


def pin_custom(spec: dict, extra: dict, boards: dict, video_log: list[dict], log) -> None:
    """Pin manual pentru un produs care nu are clip (PIN_CUSTOM = JSON cu title, link, image(s), description)."""
    if not extra.get("pinterest"):
        log("Pin manual: niciun cont Pinterest conectat.")
        return
    acc = extra["pinterest"][0]
    imgs = [i for i in ([spec.get("image")] + list(spec.get("images") or [])) if i]
    when0 = datetime.now(TZ)
    for _ in range(int(spec.get("free_slots", 0))):   # limita de 25/zi: anulez ultimul pin programat azi
        today_local = when0.date().isoformat()
        later = [(p["when"], v, p) for v in video_log for p in v.get("pins", [])
                 if p.get("id") and (p.get("when") or "")[:10] == today_local
                 and datetime.fromisoformat(p["when"]) - when0 > timedelta(minutes=10)]
        if not later:
            break
        _, v, p = max(later, key=lambda x: x[0])
        r = requests.delete(f"{API}/posts/{p['id']}", headers=headers(), timeout=30)
        if r.ok or r.status_code == 404:
            v["pins"].remove(p)
            log(f"Pin manual: am anulat pinul #{v['num']} ({p['key']}) de la {p['when'][11:16]} ca sa fac loc")
        else:
            log(f"Pin manual: nu am putut anula pinul de la {p['when'][11:16]}: {r.status_code} {r.text[:200]}")
        time.sleep(1.1)
    if spec.get("video_url"):   # pin VIDEO cu filmarea vanzatorului (demonstratie), publicat imediat
        vurl = prepare_video(spec["video_url"], log)
        if vurl:
            bid = board_for({"kw": spec.get("kw", ""), "title": spec["title"]}, BOARD_LISTS.get(acc["id"], []),
                            boards.get(acc["id"]))
            media = {"type": "video", "url": vurl}
            body = {"content": (spec.get("description", "") + "\n\n🔗 Link afiliat / Affiliate link").strip()[:800],
                    "publishNow": True, "mediaItems": [media], "platforms": [{
                        "platform": "pinterest", "accountId": acc["id"], "platformSpecificData": {
                            "boardId": bid, "title": spec["title"][:100], "link": spec["link"],
                            # coperta = un cadru din video (cu o poza AliExpress drept coperta Pinterest a refuzat pinul)
                            "coverImageKeyFrameTime": float(spec.get("cover_at", 2))}}]}
            r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=300)
            try:
                post = r.json().get("post", r.json())
                pl = [{k: x.get(k) for k in ("status", "errorMessage", "platformPostUrl")} for x in post.get("platforms", [])]
                log(f"Pin video ({spec['title'][:40]}): HTTP {r.status_code} {post.get('status')} {json.dumps(pl, ensure_ascii=False)[:500]}")
            except Exception:
                log(f"Pin video ({spec['title'][:40]}): HTTP {r.status_code} {r.text[:500]}")
        return
    if len(imgs) >= 2 and spec.get("collage", True):   # un singur pin cu colaj din mai multe poze
        price = (re.search(r"~?\s?[\d.]+ lei", spec["title"]) or [""])[0]
        col = make_collage(imgs, spec["title"], price.replace("~", "").strip() and price, log)
        if col:
            imgs = [col]
    for k, img in enumerate(imgs[:int(spec.get("count", 1))]):
        bid = board_for({"kw": spec.get("kw", ""), "title": spec["title"]}, BOARD_LISTS.get(acc["id"], []),
                        boards.get(acc["id"]))
        entry = {"platform": "pinterest", "accountId": acc["id"], "platformSpecificData": {
            "boardId": bid, "title": spec["title"][:100], "link": spec["link"]}}
        body = {"content": (spec.get("description", "") + "\n\n🔗 Link afiliat / Affiliate link").strip()[:800],
                "mediaItems": [{"type": "image", "url": img}], "platforms": [entry]}
        if k == 0:
            body["publishNow"] = True
        else:   # celelalte poze ale aceluiasi produs: la cate o ora distanta
            body.update({"scheduledFor": (when0 + timedelta(hours=k)).isoformat(), "timezone": "Europe/Bucharest"})
        r = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=120)
        try:
            post = r.json().get("post", r.json())
            st = post.get("status")
        except Exception:
            st = None
        log(f"Pin manual {k + 1} ({spec['title'][:40]}): HTTP {r.status_code} {st or r.text[:400]}")
        time.sleep(1.1)


# Boardurile tematice create automat (CREATE_BOARDS=1), daca lipsesc din cont
THEMED_BOARDS = [
    ("Curățenie inteligentă", "Aspiratoare, mopuri, roboți de geamuri și aparate cu abur de pe AliExpress, testate de mii de cumpărători. Oferte cu livrare în România."),
    ("Casă & Bucătărie", "Gadgeturi utile pentru casă și bucătărie de pe AliExpress: idei practice, prețuri mici, livrare în România."),
    ("Auto & Moto", "Accesorii auto și moto de pe AliExpress: camere de bord, suporturi de telefon, lumini ambientale. Oferte cu livrare în România."),
    ("Animale de companie", "Produse pentru câini și pisici de pe AliExpress: jucării, accesorii, îngrijire. Oferte cu livrare în România."),
    ("Gadgeturi telefon", "Accesorii și gadgeturi pentru telefon de pe AliExpress, la prețuri mici. Livrare în România."),
    ("Hobby & Outdoor", "Echipament pentru hobby, tir sportiv, camping și pescuit de pe AliExpress. Oferte cu livrare în România."),
    ("Bricolaj & Scule", "Scule și unelte de bricolaj de pe AliExpress: bormașini, șurubelnițe, accesorii. Oferte cu livrare în România."),
    ("Frumusețe", "Produse de îngrijire și frumusețe de pe AliExpress, apreciate de cumpărători. Livrare în România."),
]


def create_boards(acc: dict, log) -> None:
    r = requests.get(f"{API}/accounts/{acc['id']}/pinterest-boards", headers=headers(), timeout=30)
    have = {_plain(b.get("name", "")) for b in ((r.json().get("boards") or []) if r.ok else [])}
    for name, desc in THEMED_BOARDS:
        if _plain(name) in have:
            log(f"Board existent: {name}")
            continue
        c = requests.post(f"{API}/accounts/{acc['id']}/pinterest-boards", headers=headers(), timeout=30,
                          json={"name": name, "description": desc, "privacy": "PUBLIC"})
        log(f"Board creat: {name}" if c.ok else f"Board {name}: eroare {c.status_code} {c.text[:200]}")
        time.sleep(1.1)


def tiktok_custom(spec: dict, account: dict, log) -> None:
    """Postare TikTok manuala (TIKTOK_CUSTOM = JSON cu video_url, caption): clipul se incarca in Zernio si se
    publica imediat cu o melodie in trend din biblioteca comerciala (sunetul clipului e oricum oprit)."""
    try:
        r = requests.get(spec["video_url"], timeout=180)
        r.raise_for_status()
        pre = requests.post(f"{API}/media/presign", headers=headers(), timeout=30,
                            json={"filename": "clip.mp4", "contentType": "video/mp4", "size": len(r.content)})
        pre.raise_for_status()
        d = pre.json()
        requests.put(d["uploadUrl"], data=r.content, headers={"Content-Type": "video/mp4"}, timeout=600).raise_for_status()
    except Exception as exc:
        log(f"TikTok manual: incarcarea clipului a esuat: {str(exc)[:200]}")
        return
    tracks = trending_tracks(account, log)
    track = tracks[0] if tracks else None
    body = {"content": spec["caption"][:2150], "publishNow": True, "mediaItems": [{"type": "video", "url": d["publicUrl"]}],
            "platforms": [{"platform": "tiktok", "accountId": account["id"]}],
            "tiktokSettings": {**settings(), **(music_settings(track) if track else {})}}
    resp = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=180)
    if not resp.ok and track:
        body["tiktokSettings"] = settings()
        resp = requests.post(f"{API}/posts", headers=headers(), json=body, timeout=180)
        track = None
    try:
        post = resp.json().get("post", resp.json())
        pl = [{k: x.get(k) for k in ("status", "errorMessage", "platformPostUrl")} for x in post.get("platforms", [])]
        log(f"TikTok manual: HTTP {resp.status_code} {post.get('status')} {json.dumps(pl, ensure_ascii=False)[:400]}"
            + (f" · melodie: {track['name']} – {track['artist']}" if track else ""))
    except Exception:
        log(f"TikTok manual: HTTP {resp.status_code} {resp.text[:400]}")


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

    for pid in re.findall(r"[0-9a-f]{24}", os.environ.get("GET_POSTS", "")):   # starea unor postari (ID Zernio)
        r = requests.get(f"{API}/posts/{pid}", headers=headers(), timeout=30)
        try:
            post = r.json().get("post", r.json())
            pl = [{k: x.get(k) for k in ("platform", "status", "errorMessage", "platformPostUrl")} for x in post.get("platforms", [])]
            log(f"Postare {pid}: {post.get('status')} {json.dumps(pl, ensure_ascii=False)[:600]}")
        except Exception:
            log(f"Postare {pid}: HTTP {r.status_code} {r.text[:400]}")
    for pid in re.findall(r"[0-9a-f]{24}", os.environ.get("DELETE_POSTS", "")):   # stergere postari (ID Zernio)
        r = requests.delete(f"{API}/posts/{pid}", headers=headers(), timeout=30)
        log(f"Stergere postare {pid}: HTTP {r.status_code} {r.text[:300]}")
        time.sleep(1.1)

    if os.environ.get("CREATE_BOARDS") == "1" and extra.get("pinterest"):
        create_boards(extra["pinterest"][0], log)

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

        # starea pinurilor Pinterest de azi (publicat / in asteptare / esuat + motivul)
        report["pins"] = []
        today_iso = datetime.now(TZ).date().isoformat()
        for v in vlog:
            for pin in v.get("pins", []):
                if not pin.get("id") or (pin.get("when") or "")[:10] != today_iso:
                    continue
                time.sleep(1.1)
                try:
                    r = requests.get(f"{API}/posts/{pin['id']}", headers=headers(), timeout=30)
                    data = r.json() if r.ok else {"error": f"{r.status_code} {r.text[:300]}"}
                except Exception as exc:
                    data = {"error": str(exc)[:200]}
                post = data.get("post", data) if isinstance(data, dict) else data
                plats = post.get("platforms") if isinstance(post, dict) else None
                report["pins"].append({"num": v["num"], "key": pin["key"], "when": pin["when"][11:16],
                                       "status": post.get("status") if isinstance(post, dict) else None,
                                       "platforms": plats, "error": data.get("error") if isinstance(data, dict) else None})
        bad = [x for x in report["pins"] if x["status"] not in ("scheduled", "published", "publishing", "pending")]
        log(f"Pinuri Pinterest azi: {len(report['pins'])} verificate, {len(bad)} cu probleme")

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

    # REFRESH_POSTS="109": clip refacut (REMAKE) -> anulez programarea veche si il reprogramez la ACEEASI ora, cu clipul nou
    refresh = {int(x) for x in re.findall(r"\d+", os.environ.get("REFRESH_POSTS", ""))}
    for v in video_log:
        po = v.get("posted") or {}
        if v.get("num") in refresh and po.get("scheduledFor") and po.get("id") and not po.get("cancelled") \
                and not po.get("draft") and datetime.fromisoformat(po["scheduledFor"]) - now > timedelta(minutes=15):
            r = requests.delete(f"{API}/posts/{po['id']}", headers=headers(), timeout=30)
            if r.ok or r.status_code == 404:
                v["posted"] = None
                v["_slot"] = po["scheduledFor"]
                v["created"] = datetime.utcnow().date().isoformat()
                log(f"Clipul #{v['num']} reprogramat la {po['scheduledFor'][11:16]} cu descrierea si clipul refacute")
            else:
                log(f"Clipul #{v['num']}: nu am putut anula programarea veche ({r.status_code})")

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
    if os.environ.get("TIKTOK_CUSTOM", "").strip():
        try:
            tiktok_custom(json.loads(os.environ["TIKTOK_CUSTOM"]), account, log)
        except Exception as exc:
            log(f"TikTok manual: eroare {str(exc)[:200]}")
    if os.environ.get("PIN_CUSTOM", "").strip():
        try:
            pin_custom(json.loads(os.environ["PIN_CUSTOM"]), extra, boards, video_log, log)
        except Exception as exc:
            log(f"Pin manual: eroare {str(exc)[:200]}")
    if os.environ.get("PIN_NOW", "").strip().isdigit():
        pin_now(int(os.environ["PIN_NOW"]), extra, boards, video_log, log)
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
    if os.environ.get("RESCHEDULE_PINS") == "1" and extra["pinterest"]:
        # anulez pinurile de azi inca nepublicate; se reprogrameaza mai jos (de ex. in boardurile tematice)
        now_l = datetime.now(TZ)
        for v in video_log:
            for p in list(v.get("pins", [])):
                if p.get("id") and (p.get("when") or "")[:10] == now_l.date().isoformat() \
                        and datetime.fromisoformat(p["when"]) - now_l > timedelta(minutes=10):
                    r = requests.delete(f"{API}/posts/{p['id']}", headers=headers(), timeout=30)
                    if r.ok or r.status_code == 404:
                        v["pins"].remove(p)
                    else:
                        log(f"Nu am putut anula pinul #{v['num']} de la {p['when'][11:16]}: {r.status_code} {r.text[:150]}")
                    time.sleep(1.1)
        log("Pinurile nepublicate de azi au fost anulate pentru reprogramare")
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
