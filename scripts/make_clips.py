"""
Genereaza clipuri TikTok verticale (1080x1920) pentru produsele de pe pagina de bio.

Structura unui clip (~15 s):
  0.0 - 2.6 s  hook: poza principala cu zoom rapid + text mare care opreste scroll-ul
  2.6 - 11  s  pozele produsului (sau clipul vanzatorului) + specificatii care apar pe rand
  ultimele 3.5 s  final: pret mare + "Link in bio · produsul #N"
Pe tot clipul: pretul si "Link in bio #N" sus, @trendixeu.
Fara sunet: muzica in trend se adauga din TikTok la postare (ajuta reach-ul).
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "assets" / "fonts"
W, H, FPS = 1080, 1920, 30
FG = 1000                 # latura pozei produsului in cadru
FG_X, FG_Y = 40, 330      # pozitia pozei (zona sigura TikTok)
HOOK_D, IMG_D, VID_D, END_D, XF = 2.6, 2.4, 7.0, 3.6, 0.35

WHITE, YELLOW, DARK = (255, 255, 255, 255), (255, 214, 60, 255), (18, 16, 22, 255)
ACCENT, GOLD = (255, 90, 60, 255), (255, 195, 90, 255)


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Poppins-{weight}.ttf"), size)


# ---------------------------------------------------------------- texte

FEATURES = [
    (r"(\d+(?:[.,]\d+)?)\s?w\b", "Putere {0}W", "{0}W power"),
    (r"(\d{3,5})\s?mah", "Baterie {0} mAh", "{0} mAh battery"),
    (r"(\d+(?:[.,]\d+)?)\s?kg\b", "Până la {0} kg", "Up to {0} kg"),
    (r"(\d{2,3})\s?cm\b", "Lungime {0} cm", "{0} cm long"),
    (r"(\d)\s?in\s?1", "{0} în 1: mai multe funcții", "{0}-in-1 design"),
    (r"stainless steel", "Din oțel inoxidabil", "Stainless steel"),
    (r"waterproof|water[- ]resistant", "Rezistent la apă", "Waterproof"),
    (r"rechargeable", "Reîncărcabil prin USB", "Rechargeable via USB"),
    (r"cordless|wireless", "Fără fir", "Cordless"),
    (r"retractable", "Retractabil, sigur la depozitare", "Retractable and safe to store"),
    (r"long handle", "Coadă lungă, fără să te apleci", "Long handle, no bending"),
    (r"double[- ]sided|double[- ]head", "Față dublă, curăță mai repede", "Double-sided"),
    (r"high[- ]pressure", "Jet cu presiune mare", "High pressure"),
    (r"foldable|folding", "Pliabil, ocupă puțin loc", "Foldable, saves space"),
    (r"magnetic", "Prindere magnetică", "Magnetic mount"),
    (r"\blcd\b|digital display", "Afișaj digital", "Digital display"),
    (r"\bled\b", "Lumină LED", "LED light"),
    (r"silicone", "Din silicon flexibil", "Flexible silicone"),
    (r"adjustable", "Reglabil", "Adjustable"),
    (r"non[- ]?slip|anti[- ]?slip", "Antiderapant", "Non-slip"),
    (r"portable|\bmini\b", "Mic și ușor de luat oriunde", "Compact and portable"),
    (r"multi[- ]?function", "Multifuncțional", "Multi-functional"),
]

HASHTAGS_BASE = ["aliexpressfinds", "tiktokmademebuyit", "oferte", "reduceri", "romania", "gadgets", "fyp"]
HASHTAGS_BY_KW = {
    "cleaning": ["cleantok", "curatenie", "cleaninghacks"],
    "kitchen": ["bucatarie", "kitchengadgets", "kitchenhacks"],
    "home gadget": ["homegadgets", "casa", "homehacks"],
    "phone accessories": ["telefon", "techtok", "accesoriitelefon"],
    "car accessories": ["masina", "caraccessories", "cartok"],
    "beauty tools": ["beautytok", "frumusete", "beautyhacks"],
    "pet supplies": ["pettok", "animalutze", "petproducts"],
}


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def features(title: str) -> list[tuple[str, str]]:
    t, out = title.lower(), []
    for pattern, ro, en in FEATURES:
        m = re.search(pattern, t)
        if m:
            g = m.group(1) if m.groups() else ""
            out.append((ro.format(g), en.format(g)))
        if len(out) == 2:
            break
    return out


def build_copy(p: dict, num: int, lei: float) -> dict:
    """Hook, specificatii, descriere RO/EN si hashtag-uri pentru un produs."""
    orders, rating = int(p.get("orders") or 0), float(p.get("rating") or 0)
    titles = p.get("titles") or {}
    name_ro = titles.get("ro") or p["title"]
    name_en = titles.get("en") or p["title"]
    lei_txt = f"{lei:.0f}" if lei >= 10 else f"{lei:.2f}".replace(".", ",")

    if round(lei) < 40:
        hook_ro, hook_en = f"Doar {lei_txt} lei?!", f"Only {lei_txt} lei?!"
    elif orders >= 5000:
        hook_ro, hook_en = f"{fmt_int(orders)}+ oameni l-au comandat", f"{fmt_int(orders)}+ people ordered this"
    elif rating >= 97:
        hook_ro, hook_en = f"{rating:.0f}% recenzii pozitive", f"{rating:.0f}% positive reviews"
    else:
        hook_ro, hook_en = "Nu știai că ai nevoie de asta", "You didn't know you needed this"

    feats = features(p["title"])
    bullets_ro = [f[0] for f in feats] + [f"{rating:.0f}% recenzii pozitive · {fmt_int(orders)}+ comenzi"]
    bullets_en = [f[1] for f in feats] + [f"{rating:.0f}% positive reviews · {fmt_int(orders)}+ orders"]
    if len(bullets_ro) < 3:
        bullets_ro.append("Livrare în România")
        bullets_en.append("Ships to Romania")

    desc_ro = "\n".join([f"{hook_ro} 😱", name_ro, *[f"✅ {b}" for b in bullets_ro],
                         f"💰 Preț: ~{lei_txt} lei", f"👉 Link în bio · produsul #{num}"])
    desc_en = "\n".join([f"{hook_en} 😱", name_en, *[f"✅ {b}" for b in bullets_en],
                         f"💰 Price: ~{lei_txt} lei", f"👉 Link in bio · product #{num}"])
    tags = HASHTAGS_BASE + HASHTAGS_BY_KW.get(p.get("kw", ""), ["homegadgets", "homehacks"])
    return {"hook": hook_ro, "hook_en": hook_en, "name": name_ro, "bullets": bullets_ro,
            "description_ro": desc_ro, "description_en": desc_en, "hashtags": tags[:10],
            "price_text": f"{lei_txt} lei"}


# ---------------------------------------------------------------- grafica (straturi PNG transparente)

def wrap(text: str, f: ImageFont.FreeTypeFont, maxw: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getlength(t) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def layer() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def pill(d: ImageDraw.ImageDraw, x: int, y: int, text: str, f, bg, fg, padx=30, pady=14, anchor="left") -> int:
    bb = f.getbbox(text)
    w, h = int(f.getlength(text) + 2 * padx), int(bb[3] - bb[1] + 2 * pady)
    if anchor == "right":
        x -= w
    elif anchor == "center":
        x -= w // 2
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg)
    d.text((x + padx, y + pady - bb[1]), text, font=f, fill=fg)
    return h


def make_layers(copy: dict, num: int, tmp: Path) -> dict[str, Path]:
    out = {}

    # hook: banda inclinata usor, text mare
    img, d = layer()
    f1, f2 = font("Bold", 92), font("Medium", 46)
    lines = wrap(copy["hook"], f1, 900)[:2]
    name = wrap(copy["name"], f2, 900)[:2]
    lh1, lh2 = 108, 60
    box_h = len(lines) * lh1 + len(name) * lh2 + 70
    y0 = 360
    d.rounded_rectangle([50, y0, W - 50, y0 + box_h], radius=36, fill=(220, 40, 30, 235))
    y = y0 + 28
    for ln in lines:
        d.text(((W - f1.getlength(ln)) / 2, y), ln, font=f1, fill=WHITE)
        y += lh1
    for ln in name:
        d.text(((W - f2.getlength(ln)) / 2, y), ln, font=f2, fill=YELLOW)
        y += lh2
    out["hook"] = tmp / "l_hook.png"
    img.save(out["hook"])

    # pret + link in bio (sus, permanent dupa hook)
    img, d = layer()
    fp = font("Bold", 50)
    pill(d, 50, 220, copy["price_text"], fp, ACCENT, WHITE)
    pill(d, W - 50, 220, f"Link în bio · #{num}", fp, (255, 255, 255, 245), DARK, anchor="right")
    out["top"] = tmp / "l_top.png"
    img.save(out["top"])

    # specificatii: cate una pe strat, ca sa apara pe rand
    y = 1345
    for i, b in enumerate(copy["bullets"][:3]):
        img, d = layer()
        size = 46
        fb = font("Bold", size)
        while fb.getlength(b) > 860 and size > 30:
            size -= 2
            fb = font("Bold", size)
        txt = b
        bw = int(fb.getlength(txt)) + 120
        d.rounded_rectangle([50, y, 50 + bw, y + 74], radius=22, fill=(15, 13, 20, 215))
        d.ellipse([72, y + 22, 102, y + 52], fill=(63, 214, 138, 255))
        d.line([(80, y + 37), (86, y + 44), (96, y + 30)], fill=(15, 13, 20, 255), width=5)
        d.text((118, y + 12), txt, font=fb, fill=WHITE)
        p = tmp / f"l_b{i}.png"
        img.save(p)
        out[f"b{i}"] = p
        y += 86

    # final: pret mare + indemn
    img, d = layer()
    fbig, fmid, fsmall = font("Bold", 120), font("Bold", 56), font("Medium", 42)
    d.rounded_rectangle([90, 1140, W - 90, 1560], radius=40, fill=(15, 13, 20, 230))
    t = copy["price_text"]
    d.text(((W - fbig.getlength(t)) / 2, 1160), t, font=fbig, fill=GOLD)
    pill(d, W // 2, 1335, f"Link în bio · produsul #{num}", fmid, ACCENT, WHITE, padx=36, pady=18, anchor="center")
    s = "Caută numărul pe pagina mea"
    d.text(((W - fsmall.getlength(s)) / 2, 1468), s, font=fsmall, fill=(220, 215, 230, 255))
    # sageata spre bio (sus)
    d.polygon([(W // 2, 1050), (W // 2 - 50, 1110), (W // 2 + 50, 1110)], fill=WHITE)
    out["end"] = tmp / "l_end.png"
    img.save(out["end"])

    # @handle
    img, d = layer()
    pill(d, 50, 140, "@trendixeu", font("Medium", 34), (0, 0, 0, 120), (255, 255, 255, 235), padx=22, pady=10)
    out["handle"] = tmp / "l_handle.png"
    img.save(out["handle"])
    return out


# ---------------------------------------------------------------- video

def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr[-600:]}")


def download(url: str, dest: Path) -> Path | None:
    try:
        r = requests.get(url, timeout=40, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest
    except Exception:
        return None


def prep_image(src: Path, dest: Path) -> Path:
    """Patrat 2000x2000 pe fundal alb (pozele AliExpress au fundal alb), pentru zoom fara tremur."""
    im = Image.open(src).convert("RGB")
    # taie marginile albe ca produsul sa umple cadrul
    gray = im.convert("L").point(lambda v: 255 if v < 245 else 0)
    box = gray.getbbox()
    if box:
        pad = int(max(im.size) * 0.04)
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad))
        im = im.crop(box)
    scale = 1840 / max(im.size)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    sq = Image.new("RGB", (2000, 2000), (255, 255, 255))
    sq.paste(im, ((2000 - im.width) // 2, (2000 - im.height) // 2))
    sq.save(dest, quality=92)
    return dest


def make_background(img: Path, dest: Path) -> Path:
    """Fundal intunecat in culorile brandului, cu o urma de culoare din produs."""
    base = Image.new("RGB", (W, H))
    top, bottom = (18, 16, 22), (44, 30, 56)
    d = ImageDraw.Draw(base)
    for yy in range(H):
        t = yy / H
        d.line([(0, yy), (W, yy)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    prod = Image.open(img).convert("RGB").resize((W, W))
    glow = prod.resize((64, 64)).resize((W, W), Image.BILINEAR).filter(ImageFilter.GaussianBlur(120))
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).ellipse([220, 220, W - 220, W - 220], fill=90)
    mask = mask.filter(ImageFilter.GaussianBlur(150))
    base.paste(glow, (0, FG_Y - 40), mask)
    base.save(dest, quality=92)
    return dest


def rounded_mask(tmp: Path, size: int) -> Path:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=48, fill=255)
    p = tmp / f"mask{size}.png"
    m.save(p)
    return p


def image_segment(img: Path, dur: float, out: Path, tmp: Path, mode: str, size=FG, y=FG_Y) -> None:
    frames = int(dur * FPS)
    zexpr = {
        "in": f"min(1+0.0018*on,1.18)",
        "punch": f"if(lt(on,12),1.35-0.029*on,1.0+0.002*(on-12))",
        "out": f"max(1.18-0.0018*on,1)",
        "end": f"min(1+0.0008*on,1.06)",
    }[mode]
    x = (W - size) // 2
    mask = rounded_mask(tmp, size)
    bgimg = tmp / (img.stem + "_bg.jpg")
    if not bgimg.exists():
        make_background(img, bgimg)
    fc = (
        f"[0:v]scale={W}:{H},fps={FPS}[bg];"
        f"[1:v]zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={size}x{size}:fps={FPS},format=rgba[z];"
        f"[2:v]format=gray,scale={size}:{size}[m];[z][m]alphamerge[fg];"
        f"[bg][fg]overlay={x}:{y}:shortest=1,format=yuv420p[v]"
    )
    run(["ffmpeg", "-y", "-loglevel", "error",
         "-loop", "1", "-t", f"{dur}", "-i", str(bgimg),
         "-i", str(img),
         "-loop", "1", "-t", f"{dur}", "-i", str(mask),
         "-filter_complex", fc, "-map", "[v]", "-t", f"{dur}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])


def video_segment(src: Path, dur: float, out: Path) -> None:
    fc = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=38,"
        f"eq=brightness=-0.22:saturation=1.25,fps={FPS}[bg];"
        f"[0:v]scale={FG}:{FG}:force_original_aspect_ratio=decrease,fps={FPS}[fg];"
        f"[bg][fg]overlay=(W-w)/2:{FG_Y}+({FG}-h)/2,format=yuv420p[v]"
    )
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-t", f"{dur}", "-i", str(src),
         "-filter_complex", fc, "-map", "[v]", "-an", "-t", f"{dur}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])


def render_clip(p: dict, num: int, lei: float, out_path: Path, local_images: list[Path] | None = None) -> dict:
    """Randeaza clipul si intoarce textele (descriere, hashtag-uri)."""
    copy = build_copy(p, num, lei)
    tmp = Path(tempfile.mkdtemp(prefix="clip_"))
    try:
        # pozele produsului
        imgs: list[Path] = []
        for i, src in enumerate(local_images or []):
            imgs.append(prep_image(src, tmp / f"img{i}.jpg"))
        if not imgs:
            urls = [p.get("image")] + list(p.get("images") or [])
            for i, u in enumerate([u for u in urls if u][:5]):
                raw = download(u, tmp / f"raw{i}")
                if raw:
                    try:
                        imgs.append(prep_image(raw, tmp / f"img{i}.jpg"))
                    except Exception:
                        pass
        if not imgs:
            raise RuntimeError("nicio poza descarcata")

        segs, durs = [], []

        def add(path: Path, d: float) -> None:
            segs.append(path)
            durs.append(d)

        image_segment(imgs[0], HOOK_D, tmp / "s_hook.mp4", tmp, "punch")
        add(tmp / "s_hook.mp4", HOOK_D)

        vid = None
        if p.get("video"):
            raw = download(p["video"], tmp / "seller.mp4")
            if raw:
                try:
                    video_segment(raw, VID_D, tmp / "s_vid.mp4")
                    vid = tmp / "s_vid.mp4"
                except Exception:
                    vid = None
        middle = imgs[1:] or imgs
        if vid:
            add(vid, VID_D)
            middle = middle[:1]
        else:
            middle = (middle * 3)[:4] if len(middle) < 4 else middle[:4]
        for i, im in enumerate(middle):
            path = tmp / f"s_img{i}.mp4"
            image_segment(im, IMG_D, path, tmp, "in" if i % 2 == 0 else "out")
            add(path, IMG_D)

        image_segment(imgs[0], END_D, tmp / "s_end.mp4", tmp, "end", size=760, y=330)
        add(tmp / "s_end.mp4", END_D)

        # lipire cu tranzitii
        trans = ["fadewhite", "slideleft", "smoothleft", "slideleft", "circleopen", "smoothleft", "fade"]
        inputs, fc, prev, offset = [], [], "[0:v]", 0.0
        for s in segs:
            inputs += ["-i", str(s)]
        for i in range(1, len(segs)):
            offset += durs[i - 1] - XF
            t = trans[(i - 1) % len(trans)] if i < len(segs) - 1 else "fade"
            lbl = f"[x{i}]"
            fc.append(f"{prev}[{i}:v]xfade=transition={t}:duration={XF}:offset={offset:.3f}{lbl}")
            prev = lbl
        total = sum(durs) - XF * (len(segs) - 1)
        base = tmp / "base.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(fc),
             "-map", prev, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(FPS), str(base)])

        # straturi de text
        L = make_layers(copy, num, tmp)
        end_start = total - END_D + XF
        timeline = [
            ("handle", 0.0, total, False),
            ("hook", 0.15, HOOK_D - 0.1, False),
            ("top", HOOK_D - 0.1, end_start, False),
            ("b0", HOOK_D + 0.3, end_start, True),
            ("b1", HOOK_D + 1.9, end_start, True),
            ("b2", HOOK_D + 3.5, end_start, True),
            ("end", end_start + 0.15, total, False),
        ]
        timeline = [t for t in timeline if t[0] in L]
        args = ["-i", str(base)]
        fc, prev = [], "[0:v]"
        for i, (key, a, b, slide) in enumerate(timeline, start=1):
            args += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(L[key])]
            fade_out = f",fade=t=out:st={b - 0.2:.3f}:d=0.2:alpha=1" if b < total - 0.05 else ""
            fc.append(f"[{i}:v]format=rgba,fade=t=in:st={a:.3f}:d=0.25:alpha=1{fade_out}[l{i}]")
            x = f"'-220*max(0,1-(t-{a:.3f})/0.3)'" if slide else "0"
            fc.append(f"{prev}[l{i}]overlay=x={x}:y=0:enable='between(t,{a:.3f},{b:.3f})'[o{i}]")
            prev = f"[o{i}]"
        n_audio = len(timeline) + 1
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex", ";".join(fc),
             "-map", prev, "-map", f"{n_audio}:a", "-t", f"{total:.3f}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out_path)])
        copy["duration"] = round(total, 1)
        copy["used_seller_video"] = bool(vid)
        return copy
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
