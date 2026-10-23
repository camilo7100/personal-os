// Guarda el visor y la última copia cifrada: sin conexión o con el PC apagado se sigue viendo lo último que subió.
const CACHE = 'panel-v1';
const SHELL = ['./', 'index.html', 'viewer.js', 'viewer.css', 'manifest.webmanifest', 'favicon.ico', 'apple-touch-icon.png'];
self.addEventListener('install', (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) { return; }
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {  // con conexión: siempre lo más nuevo (y se guarda)
      const res = await fetch(req, { cache: 'no-cache' });
      if (res.ok) { cache.put(req, res.clone()); }
      return res;
    } catch (err) {
      return (await cache.match(req, { ignoreSearch: true })) || (await cache.match('index.html')) || Response.error();
    }
  })());
});
