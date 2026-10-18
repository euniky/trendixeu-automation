# trendixeu — cum funcționează (de citit la începutul unei conversații noi)

Contul TikTok **@trendixeu**: produse AliExpress cu link de afiliere. Totul rulează singur din acest repository
(`euniky/trendixeu-automation`), fără să depindă de vreo conversație cu Claude.

## Pe scurt, ce se întâmplă în fiecare zi
1. **09:00 (ora RO)**: GitHub Actions rulează `.github/workflows/daily-refresh.yml`.
2. `scripts/sync_bio.py` caută produse prin API-ul de afiliere AliExpress, le filtrează și le traduce,
   actualizează prețurile produselor adăugate manual, generează clipurile (`scripts/make_clips.py`) și
   construiește pagina de bio + consola în `public/`.
3. Site-ul se publică pe **GitHub Pages** (gratuit). **Netlify** (trendixeu.netlify.app) e doar „poarta”:
   redirecționează spre GitHub Pages și găzduiește funcțiile pentru click-uri și moderare. Netlify se
   republică doar când se schimbă `netlify/functions/*` sau `site/netlify-front/_redirects`, pentru că
   fiecare publicare costă 15 credite din cele 300/lună.
4. `scripts/post_tiktok.py` trimite clipurile la **Zernio**, care le postează pe TikTok:
   - 4 postări automate la **12:00, 14:00, 19:00, 21:00**, cu melodie în trend din biblioteca comercială
     TikTok (`musicSoundInfo`). Sunetul original al clipului e pus pe 0.
   - cele mai „hot” 2 clipuri ajung ca **schițe în inboxul TikTok** (`tiktokSettings.draft`). Utilizatorul
     le pune singur un sunet din aplicație. La schițe, TikTok ignoră melodia trimisă prin API.
   - **Instagram** (Reels), dacă e conectat în Zernio: aceleași clipuri, la aceleași ore ca pe TikTok (`post_extra`).
   - **Pinterest**, dacă e conectat în Zernio (limită Zernio: 25 de pinuri/zi/cont; automat maxim `PIN_DAILY_MAX`=20, restul rămân pentru pinuri manuale cu `pin_custom`): `schedule_pinterest` în `post_tiktok.py` programează câte un pin
     VIDEO pentru fiecare clip de azi (6/zi, `PIN_DAILY_MAX`), la orele din `PIN_TIMES` (10:30–21:30), toate cu linkul de afiliere direct (Pinterest a respins `trendixeu.netlify.app/go/<nr>` ca „pagină-intermediar”; cu un domeniu propriu se poate reveni la /go cu `PIN_LINK_MODE=go`) și mențiunea „link afiliat”. Cele mai „wow”
     clipuri merg primele la orele de vârf `PIN_PRIME` (12:00, 19:00, 20:30); pinurile aceluiași produs
     nu vin unul după altul. Include și clipurile care ajung ca schițe TikTok. Fiecare pin merge în boardul tematic potrivit (`BOARD_THEMES`: Curățenie, Bucătărie, Casă, Auto & Moto, Animale, Telefon, Frumusețe, Hobby & Outdoor, Bricolaj; potrivire după cuvinte din numele boardului, cu sau fără diacritice); altfel în boardul general: `PINTEREST_BOARD` (nume)
     sau primul cu „deal/oferte/aliexpress” în nume. `EXTRA_PLATFORMS=0` oprește Instagram și Pinterest.
5. Jurnalul clipurilor (`data/video_log.json`) se salvează înapoi în repository.

## Craciun (1 oct – 24 dec)
- Din cele 6 clipuri zilnice, `XMAS_CLIPS_PER_DAY` (2) sunt produse de Craciun PREMIUM, doar cu filmare reala: brazi
  (fibra optica, nins, cu luminite) >= 700 lei (`TREE_MIN_LEI`), restul >= 200 lei (`XMAS_OTHER_MIN_LEI`). Merg pe TikTok
  si Pinterest (board „Crăciun Premium”) ca orice clip. Cautarile sunt in `XMAS_KEYWORDS` (`sync_bio.py`).
- `scripts/christmas_pins.py` ramane pentru pinurile cu poza (3/zi) pe Pinterest.
- Clipurile obisnuite au pret minim `PREMIUM_MIN_LEI` (150 lei).

## Adrese
- Linkuri scurte pe domeniul tău: https://trendixeu.netlify.app/go/<nr produs> (se generează zilnic în `sync_bio.py`,
  duc la linkul de afiliere și numără click-ul). Folosește-le oriunde linkul trebuie scris de mână sau nu acceptă
  linkuri scurtate (Pinterest, Telegram, „Produs nr. 12” în clipuri).
- Pagina de bio: https://trendixeu.netlify.app
- Consola (pe telefon, instalabilă): https://trendixeu.netlify.app/console/
  Are clipuri de descărcat, statistici, comenzi și butoanele **Ascunde / Șterge** pentru produse (cu PIN).
- Rulări: https://github.com/euniky/trendixeu-automation/actions

## Secrete (GitHub → Settings → Secrets → Actions)
`ALI_APP_KEY`, `ALI_APP_SECRET`, `ALI_TRACKING_ID` (AliExpress Open Platform, app 549684),
`NETLIFY_AUTH_TOKEN`, `NETLIFY_SITE_ID`, `ZERNIO_API_KEY`. Opțional: `ELEVENLABS_API_KEY` (voce premium, nefolosită).

## Fișiere și ce schimbi în ele
| Ce vrei | Unde |
|---|---|
| Categorii / cuvinte de căutare | `KEYWORDS` și `CATEGORY_OF` în `scripts/sync_bio.py` |
| Categorii doar cu filmare HD | `VIDEO_ONLY` (implicit toate din `CATEGORY_OF`), pragul 720p e în `make_clips.py` |
| Filtre de calitate | `MIN_RATING_PERCENT` (90), `MIN_ORDERS` (300), `MIN_WOW_SCORE` (5) |
| Câte clipuri pe zi | `CLIPS_PER_DAY` (6, din workflow) |
| Ore de postare / câte schițe | `SLOTS`, `DRAFTS_PER_DAY` în `scripts/post_tiktok.py` |
| Melodii TikTok acceptate (moderne; fără rock, orchestră, tobe, copii, petrecere, explicit) | `GOOD_GENRES`, `BAD_WORDS` în `post_tiktok.py` |
| Muzica din clip (modernă și melodică: electronic / chill / lounge; interzise în `MUSIC_BANNED`: orchestrală, simfonică, doar tobe, copilărească) | `MUSIC_WANTED` / `MUSIC_BANNED` în `sync_bio.py`; titlurile negăsite apar în log |
| Titluri și descrieri Pinterest (cuvinte cheie căutate în română, pe tip de produs) | `PRODUCT_TYPES`, `CATEGORY_KW`, `TAGS` în `scripts/pin_seo.py` |
| Hook-uri pe tip de produs | `PRODUCT_TYPES` în `scripts/make_clips.py`; fraze generale în `HOOKS` |
| Specificații afișate în clip | `FEATURES` în `make_clips.py` (W, kPa, °C, L, 4K, 360°…) |
| Produsele adăugate manual (nr. 1–8) | `data/featured_products.json` (prețul se actualizează singur zilnic) |
| Filmare trimisă de utilizator pentru un produs | `data/seller_videos/<nr>.mp4` (fără texte peste, decupată) |
| **Tema unei zile** | `data/theme.json`: `date`, `keywords`, `min_lei`, `max_lei`, `hd_only`, `remake`, `repost`. Se aplică doar în ziua din `date`. |

## Reguli de selecție (de ce nu se repetă produsele)
- același produs de la alt vânzător e recunoscut după codul de model (WD8, V7…): un singur clip la 45 de zile;
- maxim un clip pe tip de produs la 3 zile și 2 pe categorie pe zi; piesele de schimb și accesoriile sunt excluse;
- produsele cu filmare reală au prioritate; trendul (creșterea comenzilor în ultima săptămână,
  `data/.cache/volumes.json`) dă puncte în plus;
- hook-urile nu se repetă în ultimele 40 de clipuri.

## Rulări manuale utile (Actions → Refresh bio page → Run workflow)
- `clips=0`, `zernio_check=true`: doar verificare, fără clipuri. Starea postărilor ajunge în ramura `previews`
  (`zernio.json`, `status.json`).
- `remake=16`: reface clipul 16 (cu filmarea din `data/seller_videos/16.mp4` dacă există).
- `to_drafts=16`: anulează programarea clipului 16 și îl trimite ca schiță în TikTok.
- `reschedule_music=1`: reprogramează postările de azi cu melodii în trend.
- Workflow-ul **Cauta produs pe AliExpress** (`search.yml`) caută după cuvinte sau link și dă linkuri de
  afiliere, rezoluția filmărilor și poze. Rezultatul ajunge în ramura `search`.

## Limitări cunoscute
- Pinterest nu acceptă pinuri cu mai multe poze prin API: pinurile cu poză (Crăciun, manuale) sunt un colaj 2:3 (`make_collage`): poza mare + bandă cu denumire și preț + 3 poze.
- Pinterest detectează muzica Kevin MacLeod ca având drepturi (Frequency Music) și șterge sunetul: pinurile video se încarcă FĂRĂ sunet (`muted_video` în `post_tiktok.py`).
- TikTok cere 1.000 de urmăritori pentru link în bio. Până atunci linkul nu e apăsabil; pe Pinterest e apăsabil de la primul pin.
- Instagram: cont Business/Creator conectat în Zernio. Linkurile din descriere nu se apasă, doar cel din bio.
- Prin API nu se pot pune sunete din biblioteca normală TikTok, doar din cea comercială.
- Paginile de produs AliExpress cer captcha pentru programe automate. Filmările vin doar din API sau trimise manual.
- Zernio: 60 de cereri/minut. Verificările citesc doar ultimele 3 zile.

## Preferințele utilizatorului
Răspunsuri în română. Linkuri directe spre pagina exactă de setări. Hashtag-uri ca liste de cuvinte de
ales din TikTok. Variante mai puțin cunoscute, dar practice. Sub 30 de minute pe zi de muncă manuală.
Clipuri fără voce, cu produse „wow”, hook în primele 2 secunde.
