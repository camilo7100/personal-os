# Manual de uso (versión de muestra)

PERSONAL_OS es un portal local (`http://127.0.0.1:8765`) sobre una base SQLite. Esta es la guía corta de cada página.

## Principal
- **Hoy** — alertas, la carga del día contra tu capacidad (regla 1-3-5), lo más importante y un campo de captura rápida con lenguaje natural («llamar al contador mañana 3pm ~30m p1 #Maestría»).
- **Inbox** — ideas sin clasificar; el sistema sugiere tarea, nota o descarte con reglas, sin IA.
- **Tareas** — lista, tablero (por hacer · en curso · bloqueada · hecha) y matriz de Eisenhower; subtareas, repetición y etiquetas.
- **Calendario** — mes, semana, agenda y línea de tiempo con tareas, planes, fechas de familia y festivos.
- **Revisión semanal** — lo hecho, lo atrasado y lo que viene.
- **Proyectos / Objetivos / Planes** — barras de avance; un plan convierte un temario en un cronograma con ítems diarios.

## Vida y trabajo
- **Correo** — correos clasificados por reglas (seguridad, finanzas, empleo, estudio, promoción…); solo lo que importa se marca para revisión.
- **Carrera** — empresas y postulaciones.
- **Finanzas** — importación de extractos (xlsx/pdf/csv), categorías por reglas, ingresos solo por trabajo, traslados entre cuentas fuera de ingresos y gastos, patrimonio, tarjetas, resumen por mes y por año.
- **Vida y documentos** — documentos con vencimiento (crean tareas a 30 y 15 días), suscripciones, personas y fechas especiales.

## Herramientas
- **Dispositivos** — emparejar un celular o tableta con un enlace/QR de un solo uso; el panel cifrado de solo lectura.
- **Sistema / Automatizaciones** — estado de cada job, alertas y salud.

## Probarlo con datos ficticios
```
python scripts/demo_seed.py     # crea la base de demostración
python scripts/demo_portal.py   # http://127.0.0.1:8790  ·  PIN: demo-1234
```
