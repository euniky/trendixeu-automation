"""
Titluri si descrieri de pinuri optimizate pentru cautarea Pinterest (in romana).

Pinterest e un motor de cautare: titlul si prima propozitie a descrierii trebuie sa contina exact ce scriu
oamenii in cautare ("aspirator vertical fara fir", nu "produs wow"). Aici:
  - recunosc tipul produsului din titlul englezesc AliExpress (PRODUCT_TYPES) -> denumirea cautata in romana
    + cuvinte cheie inrudite; daca nu-l recunosc, folosesc categoria (CATEGORY_KW) si titlul tradus, curatat;
  - extrag specificatiile cheie (W, kPa, mAh, L, cm, bucati...);
  - scriu 2 propozitii naturale cu cuvintele cheie, apoi rating, comenzi, pret, livrare si 3 hashtaguri.
"""
from __future__ import annotations

import re

# (regex pe titlul englezesc, denumirea cautata in romana, cuvinte cheie inrudite, beneficiul principal)
PRODUCT_TYPES: list[tuple[str, str, list[str], str]] = [
    # Craciun
    (r"fib(er|re) ?optic.{0,40}tree|tree.{0,40}fib(er|re) ?optic", "Brad artificial cu fibră optică",
     ["brad de Crăciun", "decor de Crăciun", "idei de Crăciun"], "își schimbă culorile singur și nu are nevoie de luminițe"),
    (r"(flocked|snowy?).{0,80}tree|tree.{0,80}(flocked|snowy?)", "Brad artificial nins cu luminițe",
     ["brad de Crăciun", "decor de Crăciun", "Crăciun alb"], "arată ca un brad plin de zăpadă, cu luminițe gata puse"),
    (r"christmas tree|xmas tree|pre.?lit.{0,30}tree", "Brad artificial cu luminițe încorporate",
     ["brad de Crăciun", "decor de Crăciun", "idei de Crăciun"], "vine deja luminat, fără încâlcit luminițe"),
    (r"christmas.{0,40}(curtain|icicle|string|fairy|outdoor).{0,20}light|(curtain|icicle).{0,30}light",
     "Instalație luminoasă de Crăciun", ["lumini de Crăciun", "decor exterior", "idei de Crăciun"],
     "transformă fațada casei într-o priveliște de sărbători"),
    (r"\b(reindeer|deer|elk)\b", "Ren luminos de Crăciun", ["decor exterior", "lumini de Crăciun", "idei de Crăciun"],
     "face curtea să arate ca în poveste"),
    # curatenie
    (r"carpet|upholstery|fabric.*(cleaner|washer)|spot cleaner", "Aparat de spălat covoare și tapițerie",
     ["curățare canapea", "pete pe covor", "spălat tapițerie auto"], "scoate petele din covoare și canapele fără firmă de curățenie"),
    (r"window cleaning robot|window robot|robot window|window cleaner robot|glass cleaning robot", "Robot de curățat geamuri",
     ["spălat geamuri", "geamuri curate fără efort", "curățenie de primăvară"], "spală geamurile singur, fără scară și fără dungi"),
    (r"robot vacuum|robot cleaner|sweeping robot|robotic vacuum", "Robot aspirator cu mop",
     ["aspirator robot", "curățenie automată", "casă smart"], "aspiră și spală podeaua singur, cât ești plecat"),
    (r"wet.{0,5}dry|floor washer|wet and dry", "Aspirator cu spălare umed și uscat",
     ["mop electric", "spălat gresie și parchet", "aspirator vertical"], "aspiră și spală podeaua dintr-o singură trecere"),
    (r"steam (cleaner|mop)|steamer.*clean", "Aparat de curățat cu abur",
     ["curățare cu abur", "dezinfectare fără chimicale", "curățat rosturi gresie"], "curăță și dezinfectează cu abur, fără detergenți"),
    (r"(air duster|blower|turbo fan|jet fan)", "Suflantă electrică turbo fără fir",
     ["curățat tastatură", "praf din mașină", "suflantă de mână"], "suflă praful din colțuri, tastatură și mașină în câteva secunde"),
    (r"cordless vacuum|stick vacuum|handheld vacuum|car vacuum|mini vacuum|vacuum cleaner", "Aspirator vertical fără fir",
     ["aspirator de mână", "aspirator auto", "aspirator cu acumulator"], "aspiri rapid oriunde, fără cablu"),
    (r"squeegee|silicone (broom|wiper)|magic broom|floor wiper|scraper", "Mop racletă din silicon pentru pardoseală",
     ["curățat apă de pe gresie", "racletă baie", "păr de animale de pe podea"], "strânge apa, firele de păr și murdăria dintr-o singură mișcare"),
    (r"(electric|spin|power).{0,15}(scrubber|cleaning brush)|scrubber", "Perie electrică rotativă pentru curățenie",
     ["curățat baie", "curățat gresie și rosturi", "perie rotativă"], "freci baia și bucătăria fără efort"),
    (r"(gap|groove|track|crevice).{0,20}(brush|clean)|(brush).{0,20}(gap|groove|track)", "Perie pentru rosturi și șine de geamuri",
     ["curățat rosturi", "șine geamuri", "colțuri greu accesibile"], "ajungi în colțurile unde buretele nu intră"),
    (r"melamine|magic sponge|eraser sponge|emery sponge", "Burete magic de curățat", ["pete greu de scos", "curățat fără chimicale", "trucuri curățenie"],
     "scoate petele doar cu apă"),
    (r"dish(washing)? (sponge|brush|cloth)|dishcloth|wire (dish)?cloth|scouring", "Bureți și lavete pentru vase",
     ["spălat vase", "lavete bucătărie", "curățat tigăi"], "vase curate mai repede, fără zgârieturi"),
    (r"silicone (cleaning )?brush|toilet brush|long handle brush", "Perie de curățat din silicon cu mâner lung",
     ["perie toaletă", "curățat baie", "igienă"], "se curăță ușor și nu reține murdăria"),
    (r"shoe.{0,15}(wash|laundry) bag|laundry bag|încălțăminte", "Sac pentru spălat încălțăminte în mașina de spălat",
     ["spălat adidași", "săculeț rufe", "trucuri spălătorie"], "speli adidașii în mașină fără să se strice"),
    (r"microfiber|cleaning (towel|cloth)", "Lavete din microfibră pentru curățenie",
     ["lavete microfibră", "uscat fără urme", "curățenie rapidă"], "șterg fără urme și fără scame, pe orice suprafață"),
    (r"\bmop\b", "Mop rotativ pentru curățenie", ["spălat podeaua", "mop cu găleată", "curățenie rapidă"], "speli podeaua mai repede, fără să te apleci"),
    (r"(charging port|speaker|earphone|airpods).{0,30}clean|clean(ing)? (kit|pen|set).{0,30}(phone|iphone|earphone|airpods)",
     "Kit de curățare pentru telefon și căști", ["curățat port încărcare", "curățat AirPods", "difuzor telefon înfundat"],
     "scoate praful din portul de încărcare, difuzor și căști, fără să le strici"),
    # bucatarie
    (r"vacuum seal(er|ing) bag|vacuum bag|sealer bag", "Pungi de vidat pentru alimente",
     ["păstrare alimente", "vidare carne", "sous vide"], "păstrează mâncarea proaspătă de 3–5 ori mai mult"),
    (r"vacuum sealer|food sealer", "Aparat de vidat alimente", ["pungi vidare", "păstrare alimente", "sous vide"],
     "păstrează mâncarea proaspătă de 3–5 ori mai mult"),
    (r"faucet|sink (rack|organizer|caddy)|sponge holder", "Organizator pentru chiuveta de bucătărie",
     ["suport bureți", "ordine în bucătărie", "accesorii chiuvetă"], "face ordine la chiuvetă și ține bureții uscați"),
    (r"soap dispenser|sanitizer machine|foaming", "Dozator automat de săpun cu senzor", ["dozator săpun", "baie smart", "igienă fără atingere"],
     "săpun fără să atingi nimic"),
    (r"(electric |mini )?(whisk|frother|egg beater)", "Mixer de mână pentru spumă de lapte și ouă", ["spumă lapte cafea", "batere ouă", "ustensile bucătărie"],
     "spumă de lapte ca la cafenea, în câteva secunde"),
    (r"(bag|heat) sealer|resealer|sealing machine", "Aparat de sigilat pungi", ["păstrare snacks", "pungi închise etanș", "organizare bucătărie"],
     "pungile deschise rămân închise etanș și proaspete"),
    (r"(air|vacuum) pump.{0,30}(food|fresh|seal)|(food|fresh).{0,30}(air|vacuum) pump", "Pompă electrică de vidat pentru alimente",
     ["vidare alimente", "păstrare alimente", "pungi vidat"], "scoate aerul și păstrează alimentele proaspete mai mult"),
    (r"seal(ed)? bags?.{0,10}roll|vacuum seal bags", "Pungi de vidat pentru alimente", ["păstrare alimente", "vidare carne", "sous vide"],
     "păstrează mâncarea proaspătă de 3–5 ori mai mult"),
    (r"knife sharpener", "Ascuțitor de cuțite profesional", ["cuțite ascuțite", "accesorii bucătărie", "ascuțire rapidă"],
     "cuțite ca noi în câteva secunde"),
    (r"chopper|slicer|grater|mandoline|vegetable cutter", "Tocător de legume manual",
     ["feliator legume", "gătit rapid", "ustensile bucătărie"], "tai legumele în câteva secunde, fără lacrimi"),
    (r"oil (spray|sprayer|dispenser)", "Pulverizator de ulei pentru gătit", ["air fryer", "salată", "gătit sănătos"],
     "folosești mai puțin ulei, uniform"),
    (r"air fryer", "Accesorii pentru air fryer", ["hârtie air fryer", "gătit fără ulei", "rețete air fryer"],
     "air fryer-ul rămâne curat după fiecare folosire"),
    (r"peeler|peeling", "Curățător de legume și fructe din inox", ["coajă de cartofi", "ustensile bucătărie", "gătit rapid"],
     "cureți cartofii și legumele de două ori mai repede"),
    (r"(heat|insulation|pot|hot).{0,10}(pad|mat|trivet|holder)|trivet", "Suport termoizolant din silicon pentru oale",
     ["protecție masă", "suport vase fierbinți", "accesorii bucătărie"], "pui oala fierbinte direct pe masă, fără urme"),
    (r"(coffee|tea|food|storage|sealed).{0,20}(jar|canister|container)|(jar|canister).{0,20}(coffee|tea|vacuum)",
     "Borcan ermetic pentru cafea și alimente", ["păstrare cafea", "recipiente bucătărie", "organizare cămară"],
     "cafeaua și alimentele rămân proaspete mai mult timp"),
    (r"kitchen|cooking|baking|spatula|whisk", "Gadget util pentru bucătărie",
     ["ustensile bucătărie", "idei bucătărie", "gătit ușor"], "îți scurtează timpul petrecut în bucătărie"),
    # animale
    (r"poop bag|waste bag|poo bag", "Dozator cu pungi igienice pentru plimbarea câinelui",
     ["plimbare câine", "accesorii câini", "pungi pentru căcuței"], "pungile sunt mereu la îndemână la plimbare"),
    (r"(cat|pet).{0,20}laser|laser.{0,20}(cat|pet)", "Jucărie laser automată pentru pisici",
     ["jucării pisici", "pisică activă", "joacă interactivă"], "pisica se joacă singură, chiar când nu ești acasă"),
    (r"pet hair|lint remover|fur remover", "Perie pentru părul de animale de pe haine și canapea",
     ["păr de pisică", "păr de câine", "rolă scame"], "scoate părul de animale de pe orice material"),
    (r"(water fountain|drinking fountain).*(pet|cat|dog)|(pet|cat|dog).*(fountain)", "Fântână de apă pentru pisici și câini",
     ["apă proaspătă pisică", "bol apă automat", "accesorii pisici"], "apă proaspătă și filtrată toată ziua"),
    (r"(automatic|smart).{0,15}feeder", "Hrănitor automat pentru pisici și câini", ["hrană programată", "accesorii câini", "accesorii pisici"],
     "hrana la oră fixă, chiar și când ești plecat"),
    (r"(deshedding|grooming|steam(er)?).{0,20}brush|brush.{0,20}(pet|dog|cat)", "Perie pentru câini și pisici care scoate părul mort",
     ["năpârlire", "îngrijire blană", "perie animale"], "mai puțin păr prin casă, blană frumoasă"),
    (r"nail (clipper|grinder|trimmer).{0,20}(dog|pet|cat)|(dog|pet|cat).{0,20}nail", "Pilă electrică de unghii pentru câini și pisici",
     ["tăiat unghii câine", "îngrijire animale", "fără stres"], "tai unghiile animalului fără stres și fără sângerări"),
    (r"scratch(ing)? (board|post|pad|mat|tree)|scratcher\b", "Covoraș de zgâriat pentru pisici", ["protecție canapea", "zgârietoare pisici", "accesorii pisici"],
     "pisica își ascute ghearele fără să strice canapeaua"),
    (r"(cat|pet|dog) toy|toys?.{0,20}(cat|kitten|dog)", "Jucărie interactivă pentru pisici și câini", ["jucării pisici", "pisică plictisită", "joacă interactivă"],
     "animalul se joacă singur și nu se mai plictisește"),
    (r"(dog|pet).{0,20}(bowl|feeder)|(bowl|feeder).{0,20}(dog|pet)", "Bol pliabil pentru apă și hrană pentru câini", ["plimbare câine", "accesorii câini", "drumeții cu câinele"],
     "apă și hrană pentru câine oriunde mergi"),
    (r"leash|harness|collar", "Lesă și ham pentru câini", ["plimbare câine", "accesorii câini", "ham reglabil"], "plimbări sigure și confortabile"),
    (r"\b(cat|dog|pet|puppy|kitten)s?\b", "Accesoriu util pentru câini și pisici", ["accesorii animale", "jucării câini", "jucării pisici"],
     "viață mai ușoară pentru tine și animalul tău"),
    # auto / moto
    (r"carplay|android auto", "Adaptor CarPlay și Android Auto wireless", ["CarPlay fără fir", "accesorii auto", "navigație pe display"],
     "telefonul se conectează singur la mașină, fără cablu"),
    (r"door (projector|light|logo)|welcome light", "Lumini de întâmpinare pentru portierele mașinii", ["proiector logo portieră", "accesorii auto", "tuning auto"],
     "logo luminos pe asfalt când deschizi portiera"),
    (r"headlight.{0,15}(restor|renew|polish)", "Kit de recondiționare faruri", ["faruri îngălbenite", "polish faruri", "întreținere auto"],
     "faruri limpezi ca noi, fără service"),
    (r"microfiber.{0,30}(car|towel|cloth)|car.{0,20}(drying|cleaning) (towel|cloth)", "Lavete din microfibră pentru mașină",
     ["spălat mașina", "uscat mașina fără urme", "detailing auto"], "mașina uscată fără urme și fără zgârieturi"),
    (r"dash ?cam|car dvr|driving recorder", "Cameră de bord auto", ["cameră video mașină", "înregistrare trafic", "cameră față spate"],
     "ai dovada video în caz de accident"),
    (r"head.?up display|\bhud\b", "Head-up display auto (HUD)", ["viteza pe parbriz", "accesorii auto", "GPS mașină"], "vezi viteza fără să te uiți în bord"),
    (r"starlight|star (roof|headliner)", "Plafon cu stele pentru mașină", ["lumini ambientale auto", "interior mașină", "tuning interior"],
     "interior de mașină de lux"),
    (r"ambient|atmosphere|interior (strip )?light|neon|underglow|led strip.*car|car.*led", "Lumini ambientale LED pentru mașină",
     ["iluminare interior auto", "LED auto", "tuning auto"], "interior modern, în culorile tale"),
    (r"(motorcycle|motorbike|bike|bicycle|scooter).{0,30}(phone|mobile) (holder|mount)|(phone|mobile) (holder|mount).{0,30}(motorcycle|bike|bicycle)",
     "Suport telefon pentru moto și bicicletă", ["suport GPS moto", "accesorii moto", "suport telefon bicicletă"], "telefonul stă fix, chiar și pe drum cu gropi"),
    (r"car.{0,20}(phone|mobile) (holder|mount)|(phone|mobile) (holder|mount).{0,20}car|dashboard.*holder", "Suport telefon auto",
     ["suport telefon mașină", "suport GPS", "accesorii auto"], "navigație la vedere, mâinile pe volan"),
    (r"under.?seat.{0,25}(storage|organi[sz]er|box)|seat.{0,15}(gap|bottom).{0,25}(storage|organi[sz]er|box)",
     "Organizator pentru sub scaunul mașinii", ["organizator auto", "depozitare mașină", "accesorii auto"],
     "ai loc pentru acte, șervețele și sticle, fără să ocupi bancheta"),
    (r"\bcar\b|vehicle|auto ", "Accesoriu auto util", ["accesorii auto", "gadget mașină", "interior auto"], "mașina mai practică în fiecare zi"),
    # telefon
    (r"(neck|chest|head).{0,30}(mount|holder|strap)|(mount|holder|clip).{0,30}(neck|chest|head)", "Suport telefon pentru filmare POV (gât / piept)",
     ["filmare first person", "vlog", "accesorii telefon"], "filmezi cu mâinile libere, din perspectiva ta"),
    (r"(lazy|bedside|desktop|tablet).{0,20}(stand|holder)|phone stand", "Suport flexibil pentru telefon și tabletă",
     ["suport telefon pat", "suport tabletă", "accesorii telefon"], "te uiți la filme fără să ții telefonul în mână"),
    (r"keyboard", "Tastatură wireless pentru telefon și tabletă", ["tastatură bluetooth", "accesorii tabletă", "lucru de oriunde"],
     "scrii rapid pe telefon sau tabletă, ca pe laptop"),
    (r"power ?bank", "Baterie externă pentru telefon", ["power bank", "încărcare rapidă", "baterie portabilă"], "telefonul nu se mai descarcă pe drum"),
    (r"wireless charg|magsafe|magnetic charg", "Încărcător wireless magnetic", ["încărcare fără fir", "accesorii iPhone", "încărcător rapid"],
     "pui telefonul și se încarcă, fără cabluri"),
    (r"earbud|earphone|headphone|tws", "Căști wireless", ["căști bluetooth", "căști fără fir", "căști sport"], "muzică fără fire, oriunde"),
    (r"phone|iphone|samsung|mobile", "Accesoriu pentru telefon", ["accesorii telefon", "gadgeturi telefon", "accesorii iPhone"],
     "telefonul mai practic și mai protejat"),
    # hobby / outdoor
    (r"(slingshot|catapult).{0,40}(rubber|band|tube|elastic)|(rubber|band|tube|elastic).{0,40}(slingshot|catapult)",
     "Elastice de schimb pentru praștie", ["praștie", "tir sportiv", "accesorii praștie"], "putere mai mare la fiecare tragere"),
    (r"(steel|clay|mud) balls?.{0,40}(slingshot|catapult)|(slingshot|catapult).{0,40}(steel|clay|mud) balls?",
     "Bile de oțel pentru praștie", ["muniție praștie", "tir sportiv", "accesorii praștie"], "precizie constantă la fiecare tragere"),
    (r"slingshot|catapult", "Praștie profesională din metal", ["tir sportiv", "praștie cu bile", "hobby outdoor"], "precizie și putere pentru tir sportiv la țintă"),
    (r"fishing", "Accesorii de pescuit", ["pescuit", "echipament pescuit", "hobby outdoor"], "partida de pescuit mai ușoară"),
    (r"camping|tent|hiking", "Echipament de camping", ["camping", "drumeții", "outdoor"], "mai mult confort în natură"),
    # frumusete
    (r"hair (straightener|curler|dryer)|hot brush", "Placă și perie de păr", ["coafat acasă", "păr drept", "îngrijire păr"], "coafură de salon, acasă"),
    (r"nail|manicure", "Set de manichiură", ["unghii", "manichiură acasă", "îngrijire unghii"], "unghii îngrijite, fără salon"),
    (r"ice roller|gua sha|facial.{0,20}(roller|massager)|face.{0,15}(roller|massager)", "Set ice roller și gua sha pentru față",
     ["masaj facial", "îngrijire ten", "cadou pentru ea"], "ten mai odihnit și mai luminos, în doar câteva minute"),
    (r"massag", "Aparat de masaj", ["masaj relaxare", "dureri musculare", "masaj acasă"], "relaxare după o zi lungă"),
    (r"fidget|stress relief|anti.?stress|squishy", "Jucărie antistres", ["cadou original", "relaxare", "jucării antistres"],
     "relaxează mâinile și mintea, perfectă și ca un cadou mic"),
    (r"makeup brush|cosmetic brush", "Set pensule de machiaj", ["pensule fond de ten", "machiaj", "cadou pentru ea"], "machiaj uniform, ca la salon"),
    (r"(teeth|tooth|tartar|dental).{0,20}(clean|remov)|toothbrush", "Aparat de curățat tartrul și dinții", ["igienă orală", "dinți albi", "îngrijire dentară"],
     "dinți mai curați acasă, între vizitele la dentist"),
    (r"\bfan\b", "Ventilator portabil USB reîncărcabil", ["ventilator de birou", "răcorire", "ventilator mic"], "răcoare oriunde, fără priză"),
    (r"towel (holder|rack)|towel bar", "Suport pentru prosoape autoadeziv", ["accesorii baie", "fără găurit", "organizare baie"],
     "montezi în baie fără burghiu"),
    (r"prank|gag|novelty", "Jucărie farsă amuzantă", ["cadou amuzant", "farse", "idei cadouri"], "râsete garantate cu prietenii"),
    # casa
    (r"gel cushion|seat cushion|honeycomb.{0,20}cushion|cushion", "Pernă de gel pentru scaun și birou",
     ["pernă scaun", "confort la birou", "spate sănătos"], "stai ore întregi fără să te mai doară spatele"),
    (r"night light|motion sensor light|led lamp|desk lamp", "Lampă LED cu senzor", ["iluminat casă", "lumină de veghe", "LED fără fir"],
     "lumină exact unde ai nevoie, fără cabluri"),
    (r"humidifier|diffuser", "Umidificator și difuzor de arome", ["aer umed", "aromaterapie", "dormitor"], "aer mai plăcut în casă"),
    (r"storage|organizer|organiser|rack|shelf|hook", "Organizator pentru casă", ["ordine în casă", "depozitare", "economisire spațiu"],
     "mai mult spațiu și ordine în casă"),
]

# cuvinte cheie de rezerva, dupa categoria (kw) din pipeline
CATEGORY_KW = {
    "cleaning": ("Produs de curățenie pentru casă", ["trucuri de curățenie", "casă curată", "curățenie rapidă"]),
    "kitchen": ("Gadget util pentru bucătărie", ["ustensile bucătărie", "idei bucătărie", "gătit ușor"]),
    "home gadget": ("Gadget util pentru casă", ["idei pentru casă", "gadgeturi utile", "casă practică"]),
    "pet supplies": ("Accesoriu pentru câini și pisici", ["accesorii animale", "jucării pisici", "accesorii câini"]),
    "phone accessories": ("Accesoriu pentru telefon", ["accesorii telefon", "gadgeturi telefon", "accesorii iPhone"]),
    "car accessories": ("Accesoriu auto util", ["accesorii auto", "gadget mașină", "interior auto"]),
    "beauty tools": ("Accesoriu de frumusețe", ["îngrijire personală", "frumusețe acasă", "cadou pentru ea"]),
    "professional slingshot": ("Praștie profesională din metal", ["tir sportiv", "praștie cu bile", "hobby outdoor"]),
    "craciun": ("Decorațiune premium de Crăciun", ["decor de Crăciun", "brad de Crăciun", "lumini de Crăciun"]),
}

# 3 hashtaguri pe categorie (pe Pinterest conteaza putin; prea multe arata a spam)
TAGS = {
    "cleaning": "#curatenie #trucuricuratenie #casacurata", "kitchen": "#bucatarie #gadgeturibucatarie #ideibucatarie",
    "home gadget": "#ideipentrucasa #gadgeturi #casa", "pet supplies": "#pisici #caini #accesoriianimale",
    "phone accessories": "#accesoriitelefon #gadgeturi #tech", "car accessories": "#accesoriiauto #masina #auto",
    "beauty tools": "#frumusete #ingrijire #beauty", "professional slingshot": "#prastie #tirsportiv #outdoor",
    "craciun": "#craciun #bradcraciun #decoratiunicraciun",
}

SPEC_PATTERNS = [
    (r"(\d{2,5})\s?pa\b", "{} Pa"), (r"(\d{1,3}(?:[.,]\d)?)\s?kpa\b", "{} kPa"), (r"(\d{2,5})\s?w\b", "{} W"),
    (r"(\d{3,6})\s?mah\b", "{} mAh"), (r"(\d(?:[.,]\d)?)\s?l\b", "{} L"), (r"(\d{1,2})\s?in\s?1\b", "{} în 1"),
    (r"(\d{2,4})\s?(?:pcs|pieces)\b", "{} bucăți"), (r"\b(4k|2k|1080p|1440p)\b", "{}"), (r"(\d{2,3})\s?cm\b", "{} cm"),
    (r"(\d{3})\s?°", "{}°"),
]


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


GROUP_OF_NAME = [
    (("covoare", "geamuri", "aspirator", "abur", "mop", "suflantă", "curățenie"), "cleaning"),
    (("bucătărie", "legume", "vidat", "cuțite", "ulei", "air fryer", "oale", "borcan", "chiuvet"), "kitchen"),
    (("pisici", "câini", "animale"), "pet supplies"),
    (("auto", "mașină", "moto", "bord", "hud"), "car accessories"),
    (("telefon", "căști", "wireless", "baterie"), "phone accessories"),
    (("praștie", "pescuit", "camping"), "professional slingshot"),
    (("păr", "manichiură", "masaj", "frumusețe"), "beauty tools"),
]


def group_of(name: str, kw: str | None) -> str:
    low = name.lower()
    for words, g in GROUP_OF_NAME:
        if any(w in low for w in words):
            return g
    return kw or ""


GENERIC_NAMES = {"Accesoriu auto util", "Accesoriu util pentru câini și pisici", "Accesoriu pentru telefon",
                 "Organizator pentru casă", "Gadget util pentru bucătărie"}   # tipuri "de rezerva", prea vagi pentru descriere


def detect_type(title_en: str) -> tuple[str, list[str], str] | None:
    """Tipul SPECIFIC de produs (denumire, cuvinte cheie, beneficiu) sau None daca nu e cunoscut / e prea vag."""
    t = (title_en or "").lower()
    for rx, name, kws, benefit in PRODUCT_TYPES:
        if re.search(rx, t):
            return None if name in GENERIC_NAMES else (name, kws, benefit)
    return None


def detect(title_en: str, kw: str | None) -> tuple[str, list[str], str]:
    t = (title_en or "").lower()
    for rx, name, kws, benefit in PRODUCT_TYPES:
        if re.search(rx, t):
            return name, kws, benefit
    name, kws = CATEGORY_KW.get(kw or "", ("Produs util de pe AliExpress", ["gadgeturi utile", "idei cadouri", "oferte AliExpress"]))
    return name, kws, ""


def specs(title_en: str, limit: int = 2) -> list[str]:
    out = []
    for rx, fmt in SPEC_PATTERNS:
        m = re.search(rx, title_en or "", re.I)
        if m:
            val = fmt.format(m.group(1).upper() if m.group(1).isalpha() or "k" in m.group(1).lower() else m.group(1))
            if val not in out:
                out.append(val)
        if len(out) >= limit:
            break
    return out


def price_lei(v: dict) -> str:
    c = v.get("clip") or {}
    p = c.get("price_text") or ""
    if p:
        return p.replace("≈", "~").strip()
    if v.get("amount"):
        lei = float(v["amount"]) * (4.97 if v.get("cur", "EUR") == "EUR" else 1)
        return f"~{lei:.0f} lei"
    return ""


def pin_title(v: dict) -> str:
    name, _, _ = detect(v.get("title", ""), v.get("kw"))
    sp = specs(v.get("title", ""))
    price = price_lei(v)
    t = name + (f" {', '.join(sp)}" if sp else "")
    if price and len(t) + len(price) + 3 <= 100:
        t += f" | {price}"
    return t[:100]


def pin_description(v: dict) -> str:
    name, kws, benefit = detect(v.get("title", ""), v.get("kw"))
    clip = v.get("clip") or {}
    benefit = benefit or (clip.get("benefit") or "").rstrip(".").lower() or "practic în fiecare zi"
    sp = specs(v.get("title", ""), 3)
    first = f"{name}{' de ' + sp[0] if sp else ''}: {benefit}."
    second = f"Perfect pentru {kws[0]}, {kws[1]}" + (f" sau {kws[2]}." if len(kws) > 2 else ".")
    lines = [first, second]
    if len(sp) > 1:
        lines.append("✅ " + " · ".join(sp))
    if v.get("rating") and v.get("orders"):
        lines.append(f"⭐ {float(v['rating']):.0f}% recenzii pozitive · {int(v['orders']):,}+ comenzi".replace(",", "."))
    price = price_lei(v)
    lines.append(f"💰 {price} · livrare în România" if price else "✅ Livrare în România")
    lines.append("")
    lines.append(TAGS.get(group_of(name, v.get("kw")), "#gadgeturi #ideipentrucasa #aliexpress"))
    lines.append("🔗 Link afiliat")
    return "\n".join(lines)[:800]
