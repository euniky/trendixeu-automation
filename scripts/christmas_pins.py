"""
Pinuri de Craciun: 3 produse PREMIUM pe zi in boardul Pinterest "Crăciun Premium".

Cauta zilnic pe AliExpress (API-ul de afiliere) dupa ce cauta romanii de Craciun (brazi cu fibra optica,
instalatii luminoase, perdele de lumini, decoratiuni luminoase), pastreaza doar produsele premium
(pret minim pe categorie, rating >= 92%, destule comenzi), nu repeta produsele si le programeaza prin Zernio
la orele XMAS_TIMES. Ruleaza in GitHub Actions dupa post_tiktok.py.
Are nevoie de ALI_APP_KEY, ALI_APP_SECRET, ALI_TRACKING_ID si ZERNIO_API_KEY.
  python scripts/christmas_pins.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sync_bio as sb  # noqa: E402
import post_tiktok as pt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "seasonal_log.json"   # produsele de Craciun deja postate (salvat in repo)
REPORT = ROOT / "public" / "christmas.json"
BOARD_NAME = "Crăciun Premium"
BOARD_DESC = ("Decorațiuni de Crăciun premium de pe AliExpress: brazi artificiali cu fibră optică și LED, instalații "
              "luminoase de exterior și interior, perdele de lumini, figurine luminoase. Livrare în România.")
XMAS_TIMES = [(11, 15), (17, 15), (20, 45)]      # 3 pinuri pe zi, intre pinurile obisnuite (la fix si jumatate)
PER_DAY = int(os.environ.get("XMAS_PER_DAY", "3"))
LAST_DAY = os.environ.get("XMAS_LAST_DAY", "12-24")   # dupa Ajun nu mai posteaza
REPEAT_DAYS = 60

# (cautare AliExpress, eticheta in romana = ce cauta romanii pe Google, pret minim EUR pentru "premium")
SEARCHES = [
    # brazi: doar premium, minim TREE_MIN_LEI (verificat la curs); anunturile scumpe sunt adesea noi (fara recenzii)
    ("fiber optic christmas tree", "Brad artificial cu fibră optică", 0),
    ("210cm pre lit artificial christmas tree PE PVC", "Brad artificial cu luminițe încorporate", 0),
    ("pre lit flocked christmas tree", "Brad artificial nins cu luminițe", 0),
    ("luxury full PE christmas tree with lights", "Brad artificial premium cu luminițe", 0),
    ("christmas curtain lights led outdoor", "Perdea luminoasă de Crăciun", 15),
    ("outdoor christmas lights waterproof led string", "Instalație luminoasă de exterior", 15),
    ("icicle christmas lights outdoor", "Instalație țurțuri luminoși", 15),
    ("light up reindeer christmas decoration", "Ren luminos de Crăciun", 25),
    ("christmas projector light outdoor", "Proiector de Crăciun pentru casă", 20),
    ("led christmas tree topper star", "Stea luminoasă pentru vârful bradului", 10),
    ("christmas village lighted house", "Căsuță luminoasă de Crăciun", 15),
    ("luxury christmas ornaments set", "Set globuri de Crăciun premium", 15),
    ("christmas wreath led lights", "Coroniță de Crăciun cu LED", 15),
    ("smart app christmas lights rgb", "Instalație de Crăciun smart cu aplicație", 20),
]
TREE_MIN_LEI = float(os.environ.get("TREE_MIN_LEI", "700"))   # brazii postati costa cel putin atat
MIN_RATING = 92.0
MIN_ORDERS = 20
BAD = re.compile(r"\b(sticker|wallpaper|costume|sweater|hat|socks|mold|stencil|replacement|bulb only|plug adapter)\b", re.I)
HASHTAGS = "#craciun #decoratiunicraciun #bradcraciun #instalatiicraciun #christmasdecor #aliexpressfinds"


# Denumirea pinului se ia din titlul PRODUSULUI (nu din cuvantul cautat). Produsele care nu se potrivesc
# cu niciun tip sunt sarite (ex. un brad de ceramica gasit la "brad cu fibra optica").
XMAS_TYPES = [
    (r"(ceramic|tabletop|table top|mini|desktop).{0,40}tree|tree.{0,40}(ceramic|tabletop|ornament)", None),
    (r"fib(er|re) ?optic.{0,40}tree|tree.{0,40}fib(er|re) ?optic", "Brad artificial cu fibră optică"),
    (r"(?=.*\btree)(?:(flocked|snowy?).{0,80}(pre.?lit|led|light)|(pre.?lit|led|light).{0,80}(flocked|snowy?))",
     "Brad artificial nins cu luminițe încorporate"),
    (r"(pre.?lit|led|lights?|lighted|illuminated).{0,40}(christmas )?tree|tree.{0,40}(led|lights|pre.?lit)", "Brad artificial cu luminițe încorporate"),
    (r"artificial christmas tree|christmas tree \d", "Brad artificial de Crăciun"),
    (r"curtain", "Perdea luminoasă de Crăciun"),
    (r"icicle", "Instalație țurțuri luminoși"),
    (r"reindeer|deer|elk", "Ren luminos de Crăciun"),
    (r"projector", "Proiector de Crăciun pentru casă"),
    (r"(tree )?topper|top star", "Stea luminoasă pentru vârful bradului"),
    (r"village|lighted house|christmas house", "Căsuță luminoasă de Crăciun"),
    (r"wreath|garland", "Coroniță de Crăciun cu LED"),
    (r"ornament|bauble|ball", "Globuri de Crăciun premium"),
    (r"(app|smart|bluetooth|rgb).{0,40}(light|led)", "Instalație de Crăciun smart cu aplicație"),
    (r"solar.{0,30}(light|string)", "Instalație luminoasă solară de exterior"),
    (r"string|fairy|light", "Instalație luminoasă de Crăciun"),
]


def xmas_label(title: str) -> str | None:
    t = title.lower()
    for rx, label in XMAS_TYPES:
        if re.search(rx, t):
            return label
    return None


def load_log() -> list[dict]:
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


RON = 4.97   # se actualizeaza din cursul BCE in main()


def candidates(tracking: str, used: set[str], log) -> list[dict]:
    """Produsele premium pentru fiecare cautare, cele mai bune primele."""
    groups = []
    for kw, label, min_eur in SEARCHES:
        try:
            found = sb.query_products(kw, tracking)
        except Exception as exc:
            log(f"Cautarea '{kw}' a esuat: {str(exc)[:120]}")
            continue
        good = []
        for p in found:
            pid = str(p.get("product_id"))
            title = str(p.get("product_title", ""))
            sig = sb.product_signature(title)
            try:
                rating = float(str(p.get("evaluate_rate", "0")).rstrip("%") or 0)
                price = float(p.get("target_sale_price") or p.get("sale_price") or 0)
            except ValueError:
                continue
            orders = int(p.get("lastest_volume") or 0)
            if pid in used or sig in used or not p.get("promotion_link") or BAD.search(title):
                continue
            real_label = xmas_label(title)
            if not real_label:   # nu e ce cautam (sau e un brad de ceramica / de masa)
                continue
            is_tree = real_label.startswith("Brad")
            if is_tree:
                # brad premium: pret minim in lei, mare (>= 150 cm), nu gonflabil; rating doar daca exista recenzii
                h = re.search(r"(\d{3})\s?cm|(\d(?:[.,]\d)?)\s?m\b|(\d(?:\.\d)?)\s?ft", title, re.I)
                cm = int(h.group(1)) if h and h.group(1) else (float(h.group(2).replace(",", ".")) * 100 if h and h.group(2)
                                                               else (float(h.group(3)) * 30.5 if h and h.group(3) else 0))
                if price * RON < TREE_MIN_LEI or re.search(r"inflat|ceramic|tabletop|mini", title, re.I) \
                        or (cm and cm < 150) or (orders and rating and rating < 88):
                    continue
            elif rating < MIN_RATING or orders < MIN_ORDERS or price < min_eur:
                continue
            smalls = p.get("product_small_image_urls") or []
            if isinstance(smalls, dict):
                smalls = smalls.get("string") or []
            good.append({"id": pid, "sig": sig, "kw": kw, "label": real_label, "title": title, "price": price,
                         "rating": rating, "orders": orders, "link": p["promotion_link"],
                         "image": p.get("product_main_image_url", ""),
                         "images": [u for u in smalls if isinstance(u, str)][:4],
                         # scor: popularitate si calitate (pretul mai mare = mai "premium", pana la un punct)
                         "score": (orders * (rating / 100) ** 4 * min(price / min_eur, 3)) if not is_tree else
                                  (orders * 50 + int((re.search(r"(\d{3,4})\s?(?:warm )?(?:white )?led", title, re.I) or [0, 0])[1])
                                   + int((re.search(r"(\d{3,4})\s?(?:full )?(?:branch )?tips", title, re.I) or [0, 0])[1]) / 5)})
        good.sort(key=lambda x: -x["score"])
        if good:
            groups.append(good)
        log(f"'{kw}': {len(good)} produse premium")
        time.sleep(0.5)
    # pe rand din categorii diferite, ca sa nu fie 3 brazi in aceeasi zi
    out, seen = [], set()
    for i in range(max((len(g) for g in groups), default=0)):
        for g in groups:
            if i < len(g) and g[i]["sig"] not in seen:
                seen.add(g[i]["sig"])
                out.append(g[i])
    return out


def ro_title(c: dict) -> str:
    """Titlul pinului in romana, cu termenul cautat pe Google la inceput si specificatiile cheie."""
    specs = []
    m = re.search(r"\b(\d{2,3})\s?(cm|ft)\b", c["title"], re.I)
    if m:
        specs.append(f"{m.group(1)} {m.group(2).lower()}")
    m = re.search(r"\b(\d{2,4})\s?(leds?|lights)\b", c["title"], re.I)
    if m:
        specs.append(f"{m.group(1)} LED-uri")
    m = re.search(r"\b(\d{1,3})\s?m\b", c["title"], re.I)
    if m and not specs:
        specs.append(f"{m.group(1)} m")
    t = c["label"] + " premium" + (f" – {', '.join(specs)}" if specs else "")
    return t[:100]


def description(c: dict, lei: float) -> str:
    try:
        ro = sb.translate(sb.short_title(c["title"]), "en", "ro") or ""
    except Exception:
        ro = ""
    lines = [f"🎄 {c['label']} – decor de Crăciun premium",
             ro.strip()[:180] if ro else "",
             (f"⭐ {c['rating']:.0f}% recenzii pozitive · {c['orders']}+ comenzi" if c['orders'] and c['rating'] else None),
             f"💰 Preț: ~{lei:.0f} lei · ✅ Livrare în România",
             "Comandă din timp, ca să ajungă înainte de Crăciun!",
             "", HASHTAGS, "", "🔗 Link afiliat / Affiliate link"]
    return "\n".join(l for l in lines if l is not None).replace("\n\n\n", "\n\n")[:800]  # liniile None sunt sarite


def main() -> None:
    report = {"checked": datetime.now(pt.TZ).isoformat(timespec="seconds"), "scheduled": [], "log": []}

    def log(msg: str) -> None:
        print(msg)
        report["log"].append(msg)

    def done() -> None:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    now = datetime.now(pt.TZ)
    if now.strftime("%m-%d") > LAST_DAY or now.month < 10:
        log("Sezonul de Craciun s-a terminat: nu programez pinuri.")
        return done()
    if not os.environ.get("ZERNIO_API_KEY", "").strip() or not os.environ.get("ALI_TRACKING_ID", "").strip():
        log("Lipsesc cheile Zernio sau AliExpress.")
        return done()

    accounts = pt.accounts_for("pinterest")["pinterest"]
    if not accounts:
        log("Niciun cont Pinterest conectat in Zernio.")
        return done()
    acc = accounts[0]

    # boardul "Crăciun Premium" (il creez daca lipseste)
    r = requests_get(f"{pt.API}/accounts/{acc['id']}/pinterest-boards")
    boards = (r.json().get("boards") or []) if r.ok else []
    board = next((str(b["id"]) for b in boards if pt._plain(b.get("name", "")) == pt._plain(BOARD_NAME)), None)
    if not board:
        c = pt.requests.post(f"{pt.API}/accounts/{acc['id']}/pinterest-boards", headers=pt.headers(), timeout=30,
                             json={"name": BOARD_NAME, "description": BOARD_DESC, "privacy": "PUBLIC"})
        data = c.json() if c.ok else {}
        board = str((data.get("board") or data).get("id") or "") or None
        log(f"Board creat: {BOARD_NAME}" if board else f"Nu am putut crea boardul: {c.status_code} {c.text[:200]}")
        if not board:
            return done()

    history = load_log()
    today = now.date().isoformat()
    already = [h for h in history if h.get("when", "")[:10] == today]
    slots = [datetime(now.year, now.month, now.day, h, m, tzinfo=pt.TZ) for h, m in XMAS_TIMES]
    slots = [t for t in slots if t - now >= timedelta(minutes=15) and t.isoformat() not in {h["when"] for h in already}]
    need = max(0, PER_DAY - len(already))
    if need and len(slots) < need:   # pornit tarziu: completez cu prima ora libera la :15 sau :45 (intre pinurile obisnuite)
        taken_t = {h["when"] for h in already} | {t.isoformat() for t in slots}
        extra = [datetime(now.year, now.month, now.day, h, m, tzinfo=pt.TZ) for h in range(10, 23) for m in (15, 45)]
        extra = [t for t in extra if t - now >= timedelta(minutes=15) and t.isoformat() not in taken_t
                 and all(abs((t - s).total_seconds()) >= 3 * 3600 for s in slots)]
        slots = sorted(slots + extra[:need - len(slots)])
    slots = slots[:need]
    if not slots:
        log(f"Azi sunt deja {len(already)} pinuri de Craciun programate.")
        return done()

    cutoff = (now - timedelta(days=REPEAT_DAYS)).date().isoformat()
    used = {h["id"] for h in history if h.get("when", "") >= cutoff} | \
           {h["sig"] for h in history if h.get("when", "") >= cutoff and h.get("sig")}
    sb.load_tr_cache()
    global RON
    rates = sb.get_rates()
    ron = RON = rates.get("RON", 4.97)
    pool = candidates(os.environ["ALI_TRACKING_ID"].strip(), used, log)
    for when, c in zip(slots, pool):
        img = c["image"] or (c["images"] or [None])[0]
        if not img:
            continue
        body = {"content": description(c, c["price"] * ron), "scheduledFor": when.isoformat(),
                "timezone": "Europe/Bucharest", "mediaItems": [{"type": "image", "url": img}],
                "platforms": [{"platform": "pinterest", "accountId": acc["id"], "platformSpecificData": {
                    "boardId": board, "title": ro_title(c), "link": c["link"]}}]}
        resp = pt.requests.post(f"{pt.API}/posts", headers=pt.headers(), json=body, timeout=60)
        if not resp.ok:
            log(f"Pinul de Craciun '{ro_title(c)}' a esuat: {resp.status_code} {resp.text[:300]}")
            continue
        post = resp.json().get("post", resp.json())
        history.append({"id": c["id"], "sig": c["sig"], "when": when.isoformat(), "kw": c["kw"],
                        "title": c["title"][:120], "price_eur": c["price"], "post": post.get("_id") or post.get("id")})
        report["scheduled"].append({"when": when.strftime("%H:%M"), "title": ro_title(c), "price_lei": round(c["price"] * ron),
                                    "rating": c["rating"], "orders": c["orders"]})
        log(f"Craciun {when.strftime('%d.%m %H:%M')}: {ro_title(c)} (~{c['price'] * ron:.0f} lei, "
            f"{c['rating']:.0f}%, {c['orders']} comenzi)")
        time.sleep(1.1)
    sb.save_tr_cache()
    LOG_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    done()


def requests_get(url: str):
    return pt.requests.get(url, headers=pt.headers(), timeout=30)


if __name__ == "__main__":
    main()
