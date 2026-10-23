// Comportamientos mínimos del portal (sin librerías; permitido por la CSP: script-src 'self')
document.addEventListener('change', function (e) {
  var t = e.target;
  if (t && t.hasAttribute && t.hasAttribute('data-autosubmit') && t.form) { t.form.submit(); }
});
window.addEventListener('DOMContentLoaded', function () {
  var f = document.getElementById('flash');
  if (f) { setTimeout(function () { f.remove(); }, 3500); }
});
