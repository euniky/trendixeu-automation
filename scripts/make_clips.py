"""
Generator de clipuri TikTok (1080x1920) in stil de reclama:
  - scenariu in romana, citit de o voce neurala (Microsoft Edge TTS, gratuit)
  - subtitrari cuvant-cu-cuvant sincronizate cu vocea
  - filmarile reale ale vanzatorului (cand exista), montate cu taieturi rapide
  - altfel: produsul decupat din fundal si animat (intrare, plutire, rotire, umbra)
  - carduri cu specificatii, stele de rating, contor de comenzi, final cu pret si "Link in bio #N"

Scene (fiecare corespunde unei propozitii din voce):
  hook -> nume -> specificatii -> dovada sociala -> indemn
"""
from __future__ import annotations

import asyncio
import math
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"
W, H, FPS = 1080, 1920, 30
VOICE, VOICE_RATE = "ro-RO-AlinaNeural", "+10%"
GAP = 0.12            # pauza intre propozitii (s)
END_HOLD = 1.3        # cat ramane finalul dupa ultima propozitie
CAPTION_Y = 1440      # centrul subtitrarilor (deasupra zonei cu descrierea TikTok)

WHITE, YELLOW, BLACK = (255, 255, 255, 255), (255, 214, 60, 255), (0, 0, 0, 255)
ACCENT, GOLD, PANEL = (255, 90, 60, 255), (255, 195, 90, 255), (15, 13, 20, 225)
GREEN = (63, 214, 138, 255)


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Poppins-{weight}.ttf"), size)


# =================================================================== scenariu

# (tipar in titlul englezesc, card afisat, ce spune vocea, card in engleza)
FEATURES = [
    (r"(\d+(?:[.,]\d+)?)\s?w\b", "Putere {0}W", "are o putere de {0} de wați", "{0}W power"),
    (r"(\d{3,5})\s?mah", "Baterie {0} mAh", "are baterie de {0} de miliamperi", "{0} mAh battery"),
    (r"(\d+(?:[.,]\d+)?)\s?kg\b", "Până la {0} kg", "suportă până la {0} kilograme", "Up to {0} kg"),
    (r"(\d{2,3})\s?cm\b", "Lungime {0} cm", "are {0} de centimetri", "{0} cm long"),
    (r"(\d)\s?in\s?1", "{0} în 1", "e {0} în 1", "{0}-in-1"),
    (r"stainless steel", "Oțel inoxidabil", "e din oțel inoxidabil", "Stainless steel"),
    (r"waterproof|water[- ]resistant", "Rezistent la apă", "e rezistent la apă", "Waterproof"),
    (r"rechargeable", "Reîncărcabil USB", "se încarcă prin USB", "USB rechargeable"),
    (r"cordless|wireless", "Fără fir", "merge fără fir", "Cordless"),
    (r"retractable", "Retractabil", "se strânge singur, e sigur", "Retractable"),
    (r"long handle", "Coadă lungă", "are coadă lungă, nu te mai apleci", "Long handle"),
    (r"double[- ]sided|double[- ]head", "Față dublă", "are față dublă", "Double-sided"),
    (r"high[- ]pressure", "Presiune mare", "are presiune mare", "High pressure"),
    (r"foldable|folding", "Pliabil", "se pliază și ocupă puțin loc", "Foldable"),
    (r"magnetic", "Prindere magnetică", "se prinde magnetic", "Magnetic"),
    (r"\blcd\b|digital display", "Afișaj digital", "are afișaj digital", "Digital display"),
    (r"\bled\b", "Lumină LED", "are lumină LED", "LED light"),
    (r"silicone", "Silicon flexibil", "e din silicon flexibil", "Silicone"),
    (r"adjustable", "Reglabil", "se reglează cum vrei", "Adjustable"),
    (r"non[- ]?slip|anti[- ]?slip", "Antiderapant", "nu alunecă", "Non-slip"),
    (r"portable|\bmini\b", "Compact", "e mic și îl iei oriunde", "Compact"),
    (r"multi[- ]?function", "Multifuncțional", "are mai multe funcții", "Multi-functional"),
    (r"professional", "Calitate profesională", "e de calitate profesională", "Pro quality"),
    (r"(\d{2,4})\s?(?:pcs|pieces|buc)", "{0} bucăți", "vin {0} de bucăți în pachet", "{0} pieces"),
]

HASHTAGS_BASE = ["aliexpressfinds", "tiktokmademebuyit", "oferte", "reduceri", "romania", "fyp"]
HASHTAGS_BY_KW = {
    "cleaning": ["cleantok", "curatenie", "cleaninghacks"],
    "kitchen": ["bucatarie", "kitchengadgets", "kitchenhacks"],
    "home gadget": ["gadgets", "homegadgets", "casa", "homehacks"],
    "phone accessories": ["gadgets", "telefon", "techtok", "accesoriitelefon"],
    "car accessories": ["masina", "caraccessories", "cartok"],
    "beauty tools": ["beautytok", "frumusete", "beautyhacks"],
    "pet supplies": ["pettok", "animalutze", "petproducts"],
}


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def ro_count(n: int, unit: str) -> str:
    """Acordul romanesc: 17 lei, 27 de lei, 101 lei, 120 de lei."""
    return f"{n} {unit}" if n < 20 or 1 <= n % 100 <= 19 else f"{n} de {unit}"


def ro_people(orders: int) -> str:
    if orders >= 2000:
        k = orders // 1000
        return (f"{k} mii" if k < 20 or 1 <= k % 100 <= 19 else f"{k} de mii") + " de oameni"
    n = orders // 100 * 100 if orders >= 200 else orders
    return ro_count(n, "oameni")


def tidy(name: str, words: int = 7) -> str:
    out = []
    for w in name.split()[:words]:
        out.append(w.capitalize() if w.isupper() and len(w) > 3 else w)
    return " ".join(out).rstrip(",.;:")


def features(title: str) -> list[tuple[str, str, str]]:
    t, out = title.lower(), []
    for pattern, card, spoken, en in FEATURES:
        m = re.search(pattern, t)
        if m:
            g = m.group(1) if m.groups() else ""
            out.append((card.format(g), spoken.format(g), en.format(g)))
        if len(out) == 3:
            break
    return out


def build_copy(p: dict, num: int, lei: float) -> dict:
    orders, rating = int(p.get("orders") or 0), float(p.get("rating") or 0)
    titles = p.get("titles") or {}
    name_ro = tidy(titles.get("ro") or p["title"])
    name_en = tidy(titles.get("en") or p["title"])
    li = max(1, round(lei))

    t_low = p["title"].lower()
    novelty = any(w in t_low for w in ("robot", "automatic", "magic", "lazy", "levitat", "projector",
                                        "self-", "galaxy", "smart", "laser"))
    if novelty:
        hook_show, hook_en, used = "Nu știam că există așa ceva", "I didn't know this existed", "novelty"
    elif li <= 30:
        hook_show, hook_en, used = f"Doar {li} lei?!", f"Only {li} lei?!", "price"
    elif orders >= 10000:
        hook_show, hook_en, used = f"{fmt_int(orders)}+ oameni l-au cumpărat", f"{fmt_int(orders)}+ people bought this", "orders"
    elif p.get("video"):
        hook_show, hook_en, used = "Uite ce poate face", "Watch what it does", "video"
    elif rating >= 97:
        hook_show, hook_en, used = "Nimeni nu s-a plâns de el", "Nobody complained about it", "rating"
    else:
        hook_show, hook_en, used = "Gadgetul pe care nu știai că-l vrei", "The gadget you didn't know you wanted", "generic"
    hook_say = f"Doar {ro_count(li, 'lei')} pentru asta?" if used == "price" else hook_show + "!"
    kicker = "TOP VÂNZĂRI" if orders >= 5000 else "DESCOPERIREA ZILEI"

    feats = features(p["title"])
    sentences = [("hook", hook_say), ("name", f"{name_ro}.")]
    if feats:
        parts = [f[1] for f in feats]
        txt = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " și " + parts[-1]
        sentences.append(("features", txt[0].upper() + txt[1:] + "."))
    if used == "orders":
        proof = f"Și are {rating:.0f} la sută recenzii pozitive."
    else:
        proof = f"Peste {ro_people(orders)} l-au comandat, cu {rating:.0f} la sută recenzii pozitive."
    sentences.append(("proof", proof))
    sentences.append(("cta", f"Linkul e în bio. Caută produsul numărul {num}!"))

    cards = [f[0] for f in feats] or ["Livrare în România"]
    desc_ro = "\n".join([f"{hook_show} 😱", name_ro, *[f"✅ {c}" for c in cards],
                         f"⭐ {rating:.0f}% recenzii pozitive · {fmt_int(orders)}+ comenzi",
                         f"💰 Preț: ~{li} lei", f"👉 Link în bio · caută produsul #{num}"])
    desc_en = "\n".join([f"{hook_en} 😱", name_en, *[f"✅ {f[2]}" for f in feats],
                         f"⭐ {rating:.0f}% positive reviews · {fmt_int(orders)}+ orders",
                         f"💰 Price: ~{li} lei", f"👉 Link in bio · find product #{num}"])
    tags = HASHTAGS_BASE + HASHTAGS_BY_KW.get(p.get("kw", ""), ["gadgets", "homehacks"])
    return {"hook": hook_show, "kicker": kicker, "name": name_ro, "sentences": sentences, "cards": cards,
            "orders": orders, "rating": rating, "price_text": f"{li} lei", "num": num,
            "description_ro": desc_ro, "description_en": desc_en, "hashtags": tags[:10],
            "bullets": cards}


# =================================================================== voce

ELEVEN_VOICE = "EXAVITQu4vr4xnSDxMaL"     # "Sarah": energica, merge bine in romana cu modelul multilingv
ELEVEN_MODEL = "eleven_multilingual_v2"


def _synth_eleven(text: str, mp3: Path) -> list[tuple[float, float, str]]:
    """Voce premium ElevenLabs, cu timpi pe caracter -> timpi pe cuvant."""
    import base64
    import os
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("lipseste ELEVENLABS_API_KEY")
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or ELEVEN_VOICE
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={"text": text, "model_id": ELEVEN_MODEL, "language_code": "ro",
              "voice_settings": {"stability": 0.4, "similarity_boost": 0.8, "style": 0.35, "use_speaker_boost": True}},
        timeout=90)
    if r.status_code == 400 and "language_code" in r.text:   # unele modele nu accepta codul de limba
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": ELEVEN_MODEL,
                  "voice_settings": {"stability": 0.4, "similarity_boost": 0.8, "style": 0.35, "use_speaker_boost": True}},
            timeout=90)
    if not r.ok:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    data = r.json()
    mp3.write_bytes(base64.b64decode(data["audio_base64"]))
    al = data.get("alignment") or data.get("normalized_alignment") or {}
    chars = al.get("characters") or []
    starts, ends = al.get("character_start_times_seconds") or [], al.get("character_end_times_seconds") or []
    words, cur, c_start, c_end = [], "", None, None
    for ch, a, b in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append((c_start, c_end - c_start, cur))
            cur, c_start = "", None
            continue
        if c_start is None:
            c_start = a
        cur += ch
        c_end = b
    if cur:
        words.append((c_start, c_end - c_start, cur))
    return words


def _synth_sentence(text: str, mp3: Path, provider: str = "edge") -> list[tuple[float, float, str]]:
    if provider == "elevenlabs":
        return _synth_eleven(text, mp3)
    import edge_tts

    async def go():
        comm = edge_tts.Communicate(text, VOICE, rate=VOICE_RATE, boundary="WordBoundary")
        words = []
        with open(mp3, "wb") as f:
            async for ch in comm.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    words.append((ch["offset"] / 1e7, ch["duration"] / 1e7, ch["text"]))
        return words

    return asyncio.run(go())


def _decode_pcm(mp3: Path, sr: int = 44100) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16)


def _even_words(text: str, start: float, dur: float) -> list[tuple[float, float, str]]:
    ws = text.split()
    total = sum(len(w) + 2 for w in ws) or 1
    out, t = [], start
    for w in ws:
        d = dur * (len(w) + 2) / total
        out.append((t, d, w))
        t += d
    return out


def make_voice(sentences: list[tuple[str, str]], tmp: Path, provider: str = "edge") -> dict:
    """Intoarce timpii fiecarei propozitii si ai fiecarui cuvant + fisierul audio."""
    sr = 44100
    chunks, scenes, t, ok, err = [], [], 0.0, True, None
    if provider == "none":
        ok, err = False, None
    for i, (kind, text) in enumerate(sentences):
        words, pcm = [], None
        if ok:
            try:
                mp3 = tmp / f"v{i}.mp3"
                words = _synth_sentence(text, mp3, provider)
                pcm = _decode_pcm(mp3, sr)
            except Exception as exc:
                ok, err = False, str(exc)[:240]
                print(f"[voce] indisponibila, continui fara voce: {exc}")
        if pcm is None or len(pcm) == 0:
            # fara voce: durate gandite pentru citit textul de pe ecran
            fixed = {"hook": 1.9, "proof": 1.9, "cta": 2.3}
            dur = fixed.get(kind) or min(3.2, max(1.6, 0.30 * len(text.split()) + 0.5))
            pcm = np.zeros(int(dur * sr), dtype=np.int16)
            words = []
        dur = len(pcm) / sr
        if not words:
            words = _even_words(text, 0.0, dur)
        scenes.append({"kind": kind, "text": text, "start": t, "end": t + dur,
                       "words": [(t + o, d, w) for (o, d, w) in words]})
        chunks.append(pcm)
        chunks.append(np.zeros(int(GAP * sr), dtype=np.int16))
        t += dur + GAP
    total = t + END_HOLD
    audio = np.concatenate(chunks + [np.zeros(int(END_HOLD * sr), dtype=np.int16)])
    wav = tmp / "voice.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio.tobytes())
    return {"scenes": scenes, "total": total, "wav": wav, "voiced": ok, "provider": provider if ok else None,
            "error": err if provider != "none" else None}


# =================================================================== imagini

def download(url: str, dest: Path) -> Path | None:
    try:
        r = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest
    except Exception:
        return None


def is_white_bg(im: Image.Image) -> bool:
    px = im.convert("RGB").resize((64, 64))
    a = np.asarray(px).astype(int)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    return float((border.min(axis=1) > 232).mean()) > 0.85


def cutout(im: Image.Image, size: int = 860) -> Image.Image:
    """Decupeaza produsul de pe fundal alb (umplere din margini)."""
    rgb = im.convert("RGB")
    rgb.thumbnail((1400, 1400), Image.LANCZOS)
    work = rgb.copy()
    w, h = work.size
    for xy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if min(work.getpixel(xy)) > 225:
            ImageDraw.floodfill(work, xy, (255, 0, 255), thresh=28)
    a = np.asarray(work).astype(int)
    bgm = (a[:, :, 0] == 255) & (a[:, :, 1] == 0) & (a[:, :, 2] == 255)
    mask = Image.fromarray(np.where(bgm, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    out = rgb.convert("RGBA")
    out.putalpha(mask)
    box = mask.point(lambda v: 255 if v > 30 else 0).getbbox()
    if box:
        out = out.crop(box)
    s = size / max(out.size)
    out = out.resize((max(1, int(out.width * s)), max(1, int(out.height * s))), Image.LANCZOS)
    return out


def card_image(im: Image.Image, size: int = 980) -> Image.Image:
    """Poza cu fundal propriu: card cu colturi rotunjite."""
    sq = im.convert("RGB")
    side = min(sq.size)
    sq = sq.crop(((sq.width - side) // 2, (sq.height - side) // 2, (sq.width + side) // 2, (sq.height + side) // 2))
    sq = sq.resize((size, size), Image.LANCZOS).convert("RGBA")
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=46, fill=255)
    sq.putalpha(m)
    return sq


def background(seed: Image.Image) -> Image.Image:
    top, bottom = (18, 16, 22), (46, 30, 58)
    arr = np.linspace(0, 1, H)[:, None, None]
    grad = (np.array(top) * (1 - arr) + np.array(bottom) * arr).astype(np.uint8)
    base = Image.fromarray(np.repeat(grad, W, axis=1))
    col = seed.convert("RGB").resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    glow = Image.new("RGB", (W, H), col)
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).ellipse([140, 520, W - 140, 1320], fill=110)
    m = m.filter(ImageFilter.GaussianBlur(170))
    base.paste(glow, (0, 0), m)
    return base


def shadow(w: int) -> Image.Image:
    s = Image.new("RGBA", (w + 160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(s).ellipse([80, 50, w + 80, 110], fill=(0, 0, 0, 150))
    return s.filter(ImageFilter.GaussianBlur(26))


# =================================================================== video vanzator

def probe(path: Path) -> tuple[float, int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "default=nw=1", str(path)],
                       capture_output=True, text=True)
    vals = dict(l.split("=", 1) for l in r.stdout.split() if "=" in l)
    try:
        return float(vals.get("duration", 0) or 0), int(vals.get("width", 0) or 0), int(vals.get("height", 0) or 0)
    except ValueError:
        return 0.0, 0, 0


def video_frames(path: Path, cuts: list[tuple[float, float]], portrait: bool):
    """Cadre 1080x1920 din bucati ale clipului vanzatorului (taieturi rapide)."""
    if portrait:
        args = ["-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=rgb24"]
    else:
        args = ["-filter_complex",
                f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,"
                f"eq=brightness=-0.25,fps={FPS}[bg];"
                f"[0:v]crop='min(iw,ih*0.8)':ih,scale={W}:-2,fps={FPS}[fg];"
                f"[bg][fg]overlay=0:(H-h)/2-60,format=rgb24"]
    for start, length in cuts:
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(path), "-an",
               *args, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        need, n, last = int(round(length * FPS)), 0, None
        while n < need:
            buf = proc.stdout.read(W * H * 3)
            if len(buf) < W * H * 3:
                break
            last = Image.frombuffer("RGB", (W, H), buf, "raw", "RGB", 0, 1)
            yield last
            n += 1
        proc.stdout.close()
        proc.wait()
        while n < need and last is not None:
            yield last
            n += 1


# =================================================================== grafica text

def wrap(text: str, f, maxw: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if f.getlength(t) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def text_box(text: str, f, bg, pad=36, radius=34, maxw=960) -> Image.Image:
    rows = wrap(text, f, maxw - 2 * pad)
    lh = int(f.size * 1.18)
    bw = int(max(f.getlength(t) for t in rows) + 2 * pad)
    bh = int(len(rows) * lh + 2 * pad - 6)
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=radius, fill=bg)
    y = pad - 10
    for t in rows:
        d.text(((bw - f.getlength(t)) / 2, y), t, font=f, fill=WHITE)
        y += lh
    return img


def pill(text: str, f, bg, fg, padx=28, pady=13) -> Image.Image:
    bb = f.getbbox(text)
    w, h = int(f.getlength(text) + 2 * padx), int(bb[3] - bb[1] + 2 * pady)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=bg)
    d.text((padx, pady - bb[1]), text, font=f, fill=fg)
    return img


def feature_card(text: str) -> Image.Image:
    f = font("Bold", 44)
    while f.getlength(text) > 760 and f.size > 30:
        f = font("Bold", f.size - 2)
    w = int(f.getlength(text)) + 118
    img = Image.new("RGBA", (w, 82), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, 81], radius=24, fill=(255, 255, 255, 240))
    d.ellipse([20, 21, 60, 61], fill=GREEN)
    d.line([(30, 41), (38, 50), (51, 33)], fill=(255, 255, 255, 255), width=6)
    bb = f.getbbox(text)
    d.text((76, (82 - (bb[3] - bb[1])) / 2 - bb[1]), text, font=f, fill=(20, 18, 26, 255))
    return img


def star(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill) -> None:
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    d.polygon(pts, fill=fill)


def proof_widget(rating: float, orders_shown: int, fill_frac: float, f) -> Image.Image:
    img = Image.new("RGBA", (900, 250), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 899, 249], radius=36, fill=PANEL)
    stars = 5 * fill_frac * min(1.0, rating / 100 + 0.04)
    for i in range(5):
        cx = 150 + i * 150
        star(d, cx, 80, 48, (70, 64, 82, 255))
        part = max(0.0, min(1.0, stars - i))
        if part > 0:
            lay = Image.new("RGBA", (900, 250), (0, 0, 0, 0))
            star(ImageDraw.Draw(lay), cx, 80, 48, GOLD)
            clip = Image.new("L", (900, 250), 0)
            ImageDraw.Draw(clip).rectangle([cx - 50, 0, cx - 50 + 100 * part, 250], fill=255)
            a = Image.fromarray(np.minimum(np.asarray(lay.split()[3]), np.asarray(clip)))
            img.paste(lay, (0, 0), a)
    t = f"{fmt_int(orders_shown)}+ comenzi"
    d.text(((900 - f.getlength(t)) / 2, 140), t, font=f, fill=WHITE)
    return img


class Captions:
    """Subtitrari in grupuri de pana la 3 cuvinte; cuvantul rostit e galben."""

    def __init__(self, sentences: list[list[tuple[float, float, str]]]):
        self.groups = []
        for words in sentences:          # un grup nu trece niciodata dintr-o propozitie in alta
            cur = []
            for w in words:
                cur.append(w)
                if len(cur) == 3 or re.search(r"[.,!?]$", w[2]):
                    self.groups.append(cur)
                    cur = []
            if cur:
                self.groups.append(cur)
        self.cache: dict = {}

    def render(self, gi: int, wi: int) -> Image.Image:
        key = (gi, wi)
        if key in self.cache:
            return self.cache[key]
        words = [re.sub(r"[.,!?;:]+$", "", w[2]).upper() for w in self.groups[gi]]
        f = font("Bold", 72)
        while f.getlength(" ".join(words)) > 960 and f.size > 44:
            f = font("Bold", f.size - 4)
        total = f.getlength(" ".join(words))
        img = Image.new("RGBA", (int(total) + 40, f.size + 56), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        x = 20
        for i, w in enumerate(words):
            d.text((x, 14), w, font=f, fill=YELLOW if i == wi else WHITE, stroke_width=8, stroke_fill=BLACK)
            x += f.getlength(w + " ")
        self.cache[key] = img
        return img

    def at(self, t: float):
        for gi, g in enumerate(self.groups):
            g_start = g[0][0]
            g_end = g[-1][0] + g[-1][1] + 0.12
            nxt = self.groups[gi + 1][0][0] if gi + 1 < len(self.groups) else g_end + 0.35
            if g_start <= t < max(g_end, min(nxt, g_end + 0.35)):
                wi = 0
                for i, (s, _, _) in enumerate(g):
                    if t >= s:
                        wi = i
                return self.render(gi, wi), t - g_start
        return None, 0.0


# =================================================================== compunere

def ease_out_back(x: float) -> float:
    x = max(0.0, min(1.0, x))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


def put(frame: Image.Image, layer: Image.Image, cx: float, cy: float, alpha: float = 1.0, scale: float = 1.0) -> None:
    if alpha <= 0.01:
        return
    if abs(scale - 1) > 0.01:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BILINEAR)
    a = layer.split()[3]
    if alpha < 0.99:
        a = a.point(lambda v: int(v * alpha))
    frame.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), a)


def render_clip(p: dict, num: int, lei: float, out_path: Path, local_images: list[Path] | None = None,
                sheet_path: Path | None = None, poster_path: Path | None = None, voice: str = "edge") -> dict:
    copy = build_copy(p, num, lei)
    tmp = Path(tempfile.mkdtemp(prefix="clip_"))
    try:
        # ---- materiale
        srcs: list[Image.Image] = []
        for src in local_images or []:
            srcs.append(Image.open(src))
        if not srcs:
            for i, u in enumerate([u for u in [p.get("image")] + list(p.get("images") or []) if u][:6]):
                f = download(u, tmp / f"img{i}")
                if f:
                    try:
                        im = Image.open(f)
                        im.load()
                        srcs.append(im)
                    except Exception:
                        pass
        if not srcs:
            raise RuntimeError("nicio poza descarcata")
        visuals = [("cut", cutout(im)) if is_white_bg(im) else ("card", card_image(im)) for im in srcs]
        hero = next((v for v in visuals if v[0] == "cut"), visuals[0])
        bg = background(srcs[0])

        vid_path, vid_info = None, None
        if p.get("video"):
            vp = download(p["video"], tmp / "seller.mp4")
            if vp:
                d, vw, vh = probe(vp)
                if d >= 3 and vw and vh:
                    vid_path, vid_info = vp, (d, vw, vh)

        # ---- voce + scene
        voice = make_voice(copy["sentences"], tmp, voice)
        scenes, total = voice["scenes"], voice["total"]
        sc = {s["kind"]: s for s in scenes}
        cap_kinds = ("name", "features", "proof") if voice["voiced"] else ("name", "features")
        caps = Captions([s["words"] for s in scenes if s["kind"] in cap_kinds])

        # ---- straturi
        hook_layer = text_box(copy["hook"], font("Bold", 96), (225, 38, 30, 240))
        kicker = pill(copy.get("kicker", ""), font("Bold", 40), (255, 214, 60, 255), (20, 18, 26, 255), padx=26, pady=12)
        price_pill = pill(copy["price_text"], font("Bold", 50), ACCENT, WHITE)
        num_pill = pill(f"Link în bio · #{num}", font("Bold", 46), (255, 255, 255, 245), (20, 18, 26, 255))
        handle = pill("@trendixeu", font("Medium", 34), (0, 0, 0, 120), (255, 255, 255, 235), padx=22, pady=10)
        cards = [feature_card(c) for c in copy["cards"][:3]]
        proof_font = font("Bold", 62)
        panel = Image.new("RGBA", (W - 140, 470), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle([0, 0, W - 141, 469], radius=44, fill=PANEL)
        fp = font("Bold", 150)
        price_big = Image.new("RGBA", (W, 190), (0, 0, 0, 0))
        ImageDraw.Draw(price_big).text(((W - fp.getlength(copy["price_text"])) / 2, 0), copy["price_text"], font=fp, fill=GOLD)
        end_pill = pill(f"Link în bio · produsul #{num}", font("Bold", 56), ACCENT, WHITE, padx=36, pady=18)
        fs_ = font("Medium", 42)
        sub = "Caută numărul pe pagina mea"
        end_sub = Image.new("RGBA", (W, 64), (0, 0, 0, 0))
        ImageDraw.Draw(end_sub).text(((W - fs_.getlength(sub)) / 2, 0), sub, font=fs_, fill=(225, 220, 235, 255))
        arrow = Image.new("RGBA", (120, 90), (0, 0, 0, 0))
        ImageDraw.Draw(arrow).polygon([(60, 0), (0, 70), (120, 70)], fill=WHITE)
        shadow_cache: dict = {}

        # ---- surse vizuale pe scena
        mid = [k for k in ("name", "features", "proof") if k in sc]
        mid_start, mid_end = sc[mid[0]]["start"], sc[mid[-1]]["end"] + GAP
        vgen = None
        if vid_path:
            d, vw, vh = vid_info
            length = mid_end - mid_start + 0.3
            n = max(2, int(length / 1.7))
            cl = length / n
            span = max(0.0, d - cl - 0.6)
            cuts = [(0.4 + span * i / max(1, n - 1), cl) for i in range(n)]
            vgen = video_frames(vid_path, cuts, portrait=vh / max(1, vw) >= 1.5)
        others = [v for v in visuals if v is not hero] or [hero]
        scene_visual = {"hook": hero, "name": others[0], "features": others[1 % len(others)],
                        "proof": others[2 % len(others)], "cta": hero}

        def scene_of(t: float) -> dict:
            cur = scenes[0]
            for s in scenes:
                if t >= s["start"] - GAP / 2:
                    cur = s
            return cur

        enc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
             "-i", "-", "-i", str(voice["wav"]), "-t", f"{total:.3f}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
             "-movflags", "+faststart", str(out_path)], stdin=subprocess.PIPE)

        snap_times = [0.5, sc["hook"]["end"] + 0.6, (mid_start + mid_end) / 2,
                      sc["proof"]["start"] + 0.9, total - 0.5]
        snaps, prev_kind = [], None
        for fi in range(int(total * FPS)):
            t = fi / FPS
            s = scene_of(t)
            kind, lt = s["kind"], t - s["start"]

            # ---- fundal + produs
            frame = None
            if vgen is not None and kind in ("name", "features", "proof"):
                try:
                    frame = next(vgen).copy()
                except StopIteration:
                    vgen = None
            if frame is None:
                frame = bg.copy()
                mode, vis = scene_visual[kind]
                target, cy = {"cta": (640, 760), "hook": (900, 1010)}.get(kind, (800, 980))
                if kind == "hook":
                    scl = 1.25 - 0.25 * ease(lt / 0.35) + 0.03 * lt
                else:
                    scl = (0.55 + 0.45 * ease_out_back(lt / 0.45)) * (1 + 0.025 * math.sin(t * 2.2))
                size = target * scl
                k = size / max(vis.size)
                img = vis.resize((max(1, int(vis.width * k)), max(1, int(vis.height * k))), Image.BILINEAR)
                if mode == "cut":
                    img = img.rotate(3.0 * math.sin(t * 1.6), resample=Image.BILINEAR, expand=True)
                    sw = int(img.width * 0.8) // 20 * 20
                    if sw not in shadow_cache:
                        shadow_cache[sw] = shadow(sw)
                    put(frame, shadow_cache[sw], W / 2, cy + img.height / 2 - 10, 0.9)
                bob = 16 * math.sin(t * 2.4) if mode == "cut" else 0
                put(frame, img, W / 2, cy + bob)
                if kind == "hook" and lt < 0.12:
                    frame = Image.blend(frame, Image.new("RGB", (W, H), (255, 255, 255)), 0.7 * (1 - lt / 0.12))

            if kind == "hook" and lt < 0.45:   # tremuratura scurta de camera: opreste scroll-ul
                amp = 22 * (1 - lt / 0.45)
                dx, dy = amp * math.sin(lt * 90), amp * math.cos(lt * 70)
                cw, ch = int(W / 1.05), int(H / 1.05)
                x0, y0 = (W - cw) / 2 + dx, (H - ch) / 2 + dy
                frame = frame.crop((int(x0), int(y0), int(x0) + cw, int(y0) + ch)).resize((W, H), Image.BILINEAR)
            if prev_kind is not None and kind != "hook" and lt < 0.16:   # lovitura de zoom la trecerea in scena noua
                z = 1.06 - 0.06 * (lt / 0.16)
                cw, ch = int(W / z), int(H / z)
                frame = frame.crop(((W - cw) // 2, (H - ch) // 2, (W + cw) // 2, (H + ch) // 2)).resize((W, H), Image.BILINEAR)
            prev_kind = kind

            # ---- straturi
            put(frame, handle, 50 + handle.width / 2, 150)
            if kind == "hook":
                put(frame, kicker, W / 2, 330, alpha=min(1, lt / 0.1), scale=0.7 + 0.3 * ease_out_back(lt / 0.25))
                put(frame, hook_layer, W / 2, 500, alpha=min(1, max(0, lt - 0.08) / 0.1),
                    scale=0.6 + 0.4 * ease_out_back(max(0, lt - 0.08) / 0.3))
            else:
                put(frame, price_pill, 50 + price_pill.width / 2, 245)
                put(frame, num_pill, W - 50 - num_pill.width / 2, 245)

            if "features" in sc and kind in ("features", "proof"):
                fsc = sc["features"]
                step = (fsc["end"] - fsc["start"]) / max(1, len(cards))
                fade = 1.0 if kind == "features" else max(0.0, 1 - (t - sc["proof"]["start"]) / 0.25)
                for i, c in enumerate(cards):
                    at = t - (fsc["start"] + i * step)
                    if at >= 0:
                        x = 50 + c.width / 2 - 260 * (1 - ease(at / 0.28))
                        put(frame, c, x, 350 + i * 92, alpha=min(1, at / 0.2) * fade)
            if kind == "proof":
                prog = ease(lt / 0.9)
                put(frame, proof_widget(copy["rating"], int(copy["orders"] * prog), prog, proof_font), W / 2, 430,
                    alpha=min(1, lt / 0.2), scale=0.85 + 0.15 * ease_out_back(lt / 0.35))

            if kind in cap_kinds:
                cap, age = caps.at(t)
                if cap is not None:
                    put(frame, cap, W / 2, CAPTION_Y, scale=0.88 + 0.12 * ease_out_back(age / 0.12))

            if kind == "cta":
                a = min(1, lt / 0.25)
                put(frame, panel, W / 2, 1340, alpha=a)
                put(frame, price_big, W / 2, 1215, alpha=a, scale=0.7 + 0.3 * ease_out_back(lt / 0.35))
                put(frame, end_pill, W / 2, 1395, alpha=min(1, max(0, lt - 0.15) / 0.2))
                put(frame, end_sub, W / 2, 1505, alpha=min(1, max(0, lt - 0.3) / 0.2))
                put(frame, arrow, W / 2, 1065 - 18 * abs(math.sin(t * 4)), alpha=a)

            enc.stdin.write(frame.tobytes())
            if snap_times and t >= snap_times[0]:
                snaps.append(frame.resize((270, 480)))
                snap_times.pop(0)
        enc.stdin.close()
        enc.wait()
        if enc.returncode != 0:
            raise RuntimeError("encodarea a esuat")

        if sheet_path and snaps:
            sheet = Image.new("RGB", (280 * len(snaps), 480), (30, 30, 30))
            for i, sn in enumerate(snaps):
                sheet.paste(sn, (i * 280, 0))
            sheet.save(sheet_path, quality=82)
        if poster_path and len(snaps) > 1:
            snaps[1].resize((540, 960)).save(poster_path, quality=80)

        copy.update({"duration": round(total, 1), "used_seller_video": bool(vid_path), "voiced": voice["voiced"],
                     "voice": voice.get("provider"), "voice_error": voice.get("error"),
                     "script": [s["text"] for s in scenes]})
        copy.pop("sentences", None)
        return copy
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
