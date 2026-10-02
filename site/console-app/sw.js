// Aplicatia consolei: deschide ultima versiune salvata cand nu ai internet.
const CACHE = "trendixeu-console-v1";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/console/", "/console/icon-192.png"])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/clips/")) return; // clipurile se descarca direct, nu se pastreaza in aplicatie
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok && (url.pathname === "/console/" || url.pathname === "/status.json")) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(url.pathname, copy));
        }
        return res;
      })
      .catch(() => caches.match(url.pathname))
  );
});
