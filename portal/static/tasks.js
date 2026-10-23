// Gestor de tareas: vista previa de la captura en lenguaje natural, arrastrar tareas al calendario y cronómetro de enfoque.
// Sin librerías; permitido por la CSP (script-src 'self').
(function () {
  function post(url, data) {
    var body = new URLSearchParams(data);
    return fetch(url, { method: 'POST', body: body, credentials: 'same-origin', headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
  }

  // App sin conexión: guarda lo último que viste (solo en el celular/HTTPS; en el PC no hace falta)
  if ('serviceWorker' in navigator && location.protocol === 'https:') { navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {}); }

  // 0) Atajos de teclado (fuera de los campos de texto): n = nueva tarea · t tareas · c calendario · h hoy · r revisión
  document.addEventListener('keydown', function (e) {
    var a = document.activeElement, typing = a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT');
    if (typing || e.ctrlKey || e.metaKey || e.altKey) { return; }
    var go = { t: '/tasks', c: '/calendar', h: '/', r: '/review' }[e.key];
    if (go) { location.href = go; return; }
    if (e.key === 'n' || e.key === 'q') {
      var q = document.querySelector('[data-quick]');
      if (q) { e.preventDefault(); q.focus(); } else { location.href = '/tasks'; }
    }
  });

  // 1) Captura rápida: muestra al instante lo que se entendió (fecha, hora, duración, prioridad, proyecto, etiquetas)
  document.querySelectorAll('[data-quick]').forEach(function (input) {
    var box = document.querySelector(input.getAttribute('data-quick'));
    var timer = null;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var q = input.value.trim();
        if (!q) { box.textContent = ''; return; }
        fetch('/api/quick/parse?text=' + encodeURIComponent(q), { credentials: 'same-origin' })
          .then(function (r) { return r.json(); })
          .then(function (j) { box.textContent = j.summary ? '→ ' + j.title + '   ' + j.summary : ''; })
          .catch(function () {});
      }, 180);
    });
  });

  // 2) Arrastrar una tarea (de la lista lateral o de un bloque) a un día/hora de la cuadrícula
  var dragged = null;
  document.querySelectorAll('[data-task]').forEach(function (el) {
    el.addEventListener('dragstart', function (e) {
      dragged = el.getAttribute('data-task');
      e.dataTransfer.setData('text/plain', dragged);
      e.dataTransfer.effectAllowed = 'move';
    });
  });
  document.querySelectorAll('[data-day]').forEach(function (col) {
    col.addEventListener('dragover', function (e) { e.preventDefault(); col.classList.add('drop'); });
    col.addEventListener('dragleave', function () { col.classList.remove('drop'); });
    col.addEventListener('drop', function (e) {
      e.preventDefault();
      col.classList.remove('drop');
      var id = dragged || e.dataTransfer.getData('text/plain');
      if (!id) { return; }
      var rect = col.getBoundingClientRect();
      var from = parseInt(col.getAttribute('data-from'), 10), range = parseInt(col.getAttribute('data-range'), 10);
      var mins = from + Math.round(((e.clientY - rect.top) / rect.height) * range / 15) * 15;
      var hh = ('0' + Math.floor(mins / 60)).slice(-2), mm = ('0' + (mins % 60)).slice(-2);
      post('/tasks/' + id + '/schedule', { day: col.getAttribute('data-day'), time: hh + ':' + mm, back: location.pathname + location.search })
        .then(function () { location.reload(); });
    });
  });

  // 3) Cronómetro de enfoque (Pomodoro 25 min): al detener suma el tiempo a la tarea
  var t = document.querySelector('[data-timer]');
  if (t) {
    var id = t.getAttribute('data-timer'), shown = t.querySelector('.time'), btn = t.querySelector('.start'), stop = t.querySelector('.stop');
    var started = null, acc = 0, tick = null, goal = 25 * 60;
    function render() {
      var s = acc + (started ? Math.floor((Date.now() - started) / 1000) : 0);
      shown.textContent = ('0' + Math.floor(s / 60)).slice(-2) + ':' + ('0' + (s % 60)).slice(-2);
      if (s === goal && started) { try { new Notification('PERSONAL_OS', { body: '25 minutos de enfoque: descansa 5.' }); } catch (e) {} document.title = '⏰ Descanso · PERSONAL_OS'; }
    }
    btn.addEventListener('click', function () {
      if (started) { acc += Math.floor((Date.now() - started) / 1000); started = null; clearInterval(tick); btn.textContent = 'Continuar'; }
      else { started = Date.now(); tick = setInterval(render, 1000); btn.textContent = 'Pausa'; try { Notification.requestPermission(); } catch (e) {} }
      render();
    });
    stop.addEventListener('click', function () {
      var s = acc + (started ? Math.floor((Date.now() - started) / 1000) : 0);
      var minutes = Math.round(s / 60);
      if (minutes < 1) { acc = 0; started = null; clearInterval(tick); render(); return; }
      post('/tasks/' + id + '/focus', { minutes: minutes, back: '/tasks/' + id }).then(function () { location.href = '/tasks/' + id + '?ok=' + encodeURIComponent(minutes + ' min de enfoque guardados'); });
    });
  }
})();
