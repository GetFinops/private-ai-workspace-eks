/**
 * Service worker — network-first caching for shell assets.
 * Adapted from pattern in pewdiepie-archdaemon/odysseus static/sw.js (MIT).
 */
var CACHE = 'pai-shell-v1';
var SHELL = ['/static/style.css', '/static/app.js', '/static/manifest.json'];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) { return c.addAll(SHELL); })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function (e) {
  // Only cache GET requests for static assets; pass API calls through.
  var url = e.request.url;
  if (e.request.method !== 'GET') return;
  if (url.includes('/v1/') || url.includes('/auth/') || url.includes('/config.json')) return;

  e.respondWith(
    fetch(e.request)
      .then(function (resp) {
        if (resp && resp.status === 200 && SHELL.some(function (s) { return url.endsWith(s); })) {
          var clone = resp.clone();
          caches.open(CACHE).then(function (c) { c.put(e.request, clone); });
        }
        return resp;
      })
      .catch(function () { return caches.match(e.request); })
  );
});
