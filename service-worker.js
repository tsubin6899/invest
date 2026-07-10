const CACHE_NAME = "tsubin-assets-dashboard-invest-v32";
const CORE_ASSETS = [
  "./",
  "./index.html",
  "./combined-dashboard.html",
  "./personal-assets-dashboard.html",
  "./accounting-app.html",
  "./accounting-manifest.webmanifest",
  "./market-data-config.js",
  "./manifest.webmanifest",
  "./latest-prices.json",
  "./latest-rates.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/accounting-192.png",
  "./icons/accounting-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => Promise.allSettled(CORE_ASSETS.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.pathname.includes("/api/")) return;
  if (url.pathname.endsWith("/latest-prices.json") || url.pathname.endsWith("/latest-rates.json")) {
    event.respondWith(fetch(event.request, { cache: "no-store" }).catch(() => caches.match(event.request)));
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match("./index.html")))
  );
});
