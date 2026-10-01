# trendixeu-automation

Reconstruiește automat pagina de bio (`public/index.html`) în fiecare zi, dimineața,
combinând:
- **produsele fixe** din `data/featured_products.json` (cele din videoclipurile tale — le
  editezi tu manual când vrei să schimbi ce apare sus pe pagină)
- **produsele "hot sales"** luate live din AliExpress Affiliate API, filtrate după rating
  minim (4.5) și număr minim de comenzi (300) — vezi constantele din `scripts/sync_bio.py`
  dacă vrei să schimbi pragurile.

## Ce faci o singură dată (setup)

1. **Obții acces la AliExpress Open Platform** (portals.aliexpress.com → Open Platform / API)
   și notezi `App Key`, `App Secret` și `Tracking ID`-ul din contul tău de afiliere.
2. **Creezi un repo pe GitHub** (gratuit) și urci fișierele din acest folder.
3. **Creezi un token Netlify** (User settings → Applications → New access token) și afli
   `Site ID`-ul site-ului trendixeu.netlify.app (Site settings → General → Site details).
4. **Adaugi 5 secrete în repo** (Settings → Secrets and variables → Actions → New repository
   secret): `ALI_APP_KEY`, `ALI_APP_SECRET`, `ALI_TRACKING_ID`, `NETLIFY_AUTH_TOKEN`,
   `NETLIFY_SITE_ID`.
5. **Pornești workflow-ul manual o dată** (tab Actions → "Refresh bio page" → Run workflow),
   ca să verifici că totul merge. După asta rulează singur, zilnic, la 09:00.

## Ce rămâne manual, mereu

- Completezi/actualizezi `data/featured_products.json` când ai un videoclip nou (link, preț,
  titlu, poză).
- Producerea clipurilor TikTok în sine (research săptămânal + overlay) rămâne fluxul deja
  stabilit cu Claude.
