// Visor del panel personal: descarga data.bin (cifrado con AES-256-GCM), lo descifra AQUÍ con la llave de este equipo y muestra las páginas
// en un iframe sin scripts. La llave nunca sale del navegador: llega una sola vez en el #fragmento del enlace de emparejamiento.
(function () {
  'use strict';
  var KEY_NAME = 'panel-key', bundle = null;
  var $ = function (id) { return document.getElementById(id); };

  function b64u(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '=';
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function say(title, text) {
    $('page').hidden = true; $('bar').hidden = true; $('msg').hidden = false;
    $('msg').innerHTML = '<h1></h1><p></p>';
    $('msg').firstChild.textContent = title; $('msg').lastChild.textContent = text;
  }
  function getKey() { try { return localStorage.getItem(KEY_NAME); } catch (e) { return null; } }

  async function load() {
    var m = /[#&]k=([A-Za-z0-9_-]{40,})/.exec(location.hash);
    if (m) {  // emparejamiento: guarda la llave en este equipo y la quita de la barra de direcciones
      try { localStorage.setItem(KEY_NAME, m[1]); } catch (e) { say('No se pudo guardar', 'Este navegador bloquea el almacenamiento. Usa el modo normal (no privado).'); return; }
      history.replaceState(null, '', location.pathname + location.search);
    }
    var key = getKey();
    if (!key) { say('Panel cifrado', 'Este equipo todavía no está emparejado. Pide el enlace de emparejamiento desde tu PC (Dispositivos → Panel fuera del PC) y ábrelo aquí.'); return; }
    say('Descifrando…', '');
    try {
      var res = await fetch('data.bin', { cache: 'no-cache' });
      if (!res.ok) throw new Error('http ' + res.status);
      var buf = new Uint8Array(await res.arrayBuffer());
      var ck = await crypto.subtle.importKey('raw', b64u(key), 'AES-GCM', false, ['decrypt']);
      var plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.slice(0, 12) }, ck, buf.slice(12));
      var ds = new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream('gzip')));
      bundle = JSON.parse(await ds.text());
    } catch (e) {
      say('No se pudo abrir', 'La llave de este equipo no coincide con la copia (¿se renovó la llave?) o no hay conexión. Pide un enlace de emparejamiento nuevo desde tu PC.');
      return;
    }
    $('msg').hidden = true; $('bar').hidden = false;
    var gen = new Date(bundle.generated), days = (Date.now() - gen.getTime()) / 864e5;
    $('when').textContent = 'Copia de solo lectura del ' + gen.toLocaleString('es-CO', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) +
      (days >= 2 ? ' · tu PC lleva ' + Math.floor(days) + ' días sin subir una copia nueva' : '');
    $('bar').className = days >= 2 ? 'old' : '';
    show();
  }

  function show() {
    if (!bundle) return;
    var key = decodeURIComponent((location.hash || '').replace(/^#/, '')) || '/';
    var html = bundle.pages[key] || bundle.pages[key.replace(/\/$/, '')];
    if (!html) html = '<body style="font:16px system-ui;padding:24px"><h2>Esta página no está en la copia</h2><p>' + key.replace(/[<>&]/g, '') + '</p><p><a href="#/" target="_top">Ir al inicio</a></p></body>';
    $('page').hidden = false; $('page').srcdoc = html;
  }

  $('forget').addEventListener('click', function () {
    try { localStorage.removeItem(KEY_NAME); } catch (e) {}
    bundle = null; say('Equipo olvidado', 'La llave se borró de este equipo. Para volver a entrar necesitas un enlace de emparejamiento nuevo.');
  });
  window.addEventListener('hashchange', function () { if (bundle) show(); else if (/[#&]k=/.test(location.hash)) load(); });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(function () {});
  load();
})();
