// PERSONAL_OS como app: guarda en el celular lo último que viste y lo muestra aunque tu PC esté apagado.
// Solo páginas de consulta (Hoy, Tareas, Calendario, Revisión, Proyectos, Planes, Objetivos, Manual, Finanzas, Correo, Carrera, Vida, Estudio). Se borran al cerrar sesión o desconectar el dispositivo.
// Con el PC encendido: siempre trae lo más reciente (y lo guarda). Con el PC apagado o sin red: muestra la copia guardada con un aviso.
const CACHE = 'pos-v2';
const OK = [/^\/$/, /^\/tasks(\/\d+)?$/, /^\/calendar$/, /^\/review$/, /^\/projects$/, /^\/plans(\/\d+)?$/, /^\/goals$/, /^\/manual$/, /^\/inbox$/, /^\/money$/, /^\/mail$/, /^\/career$/, /^\/life$/, /^\/study$/];
const TIMEOUT_MS = 4000;

self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });

function saved(url) { return new Date(url).toLocaleString('es-CO', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); }

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) { return; }
  if (url.pathname === '/logout') {  // al cerrar sesión se borra todo lo guardado
    event.waitUntil(caches.delete(CACHE));
    return;
  }
  if (req.mode !== 'navigate' || !OK.some((r) => r.test(url.pathname))) { return; }
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const key = url.pathname + url.search;
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
      const res = await fetch(req, { signal: ctl.signal, credentials: 'same-origin' });
      clearTimeout(t);
      if (res.ok && !res.redirected && (res.headers.get('content-type') || '').includes('text/html')) {
        const copy = res.clone();
        const body = await copy.text();
        await cache.put(key, new Response(body, { headers: { 'content-type': 'text/html; charset=utf-8', 'x-saved-at': new Date().toISOString() } }));
      }
      return res;
    } catch (err) {
      const hit = await cache.match(key);
      if (!hit) {
        return new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font:16px system-ui;padding:24px"><h2>📴 Tu PC está apagado o sin conexión</h2><p>Aún no tengo guardada esta página. Cuando tu PC se encienda y abras el portal, quedará guardada.</p><p>Mientras tanto: Google Calendar te avisa de lo importante y Telegram guarda tus mensajes.</p></body>', { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } });
      }
      const html = await hit.text();
      const when = saved(hit.headers.get('x-saved-at'));
      const banner = '<div style="background:#fff3cd;color:#664d03;border-radius:12px;padding:12px 14px;margin:0 0 16px;font-size:14px">📴 <b>Tu PC está apagado o sin conexión.</b> Te muestro lo último que viste (' + when + '). Se actualiza solo cuando el PC se encienda y abras el portal. Lo que captures ahora, mándalo a Telegram: llega cuando el PC encienda.</div>';
      return new Response(html.replace('<main>', '<main>' + banner), { headers: { 'content-type': 'text/html; charset=utf-8' } });
    }
  })());
});
