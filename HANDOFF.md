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
5. Jurnalul clipurilor (`data/video_log.json`) se salvează înapoi în repository.

## Adrese
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
| Melodii TikTok acceptate (doar serioase, fără copii / petrecere / explicit) | `GOOD_GENRES`, `BAD_WORDS` în `post_tiktok.py` |
| Muzica de rezervă din clip (doar serioasă; cea comică e în `MUSIC_BANNED` și se șterge din cache) | `MUSIC_WANTED` / `MUSIC_BANNED` în `sync_bio.py`; titlurile negăsite apar în log |
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
- TikTok cere 1.000 de urmăritori pentru link în bio. Până atunci linkul nu e apăsabil.
- Prin API nu se pot pune sunete din biblioteca normală TikTok, doar din cea comercială.
- Paginile de produs AliExpress cer captcha pentru programe automate. Filmările vin doar din API sau trimise manual.
- Zernio: 60 de cereri/minut. Verificările citesc doar ultimele 3 zile.

## Preferințele utilizatorului
Răspunsuri în română. Linkuri directe spre pagina exactă de setări. Hashtag-uri ca liste de cuvinte de
ales din TikTok. Variante mai puțin cunoscute, dar practice. Sub 30 de minute pe zi de muncă manuală.
Clipuri fără voce, cu produse „wow”, hook în primele 2 secunde.
