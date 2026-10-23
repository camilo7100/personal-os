-- PERSONAL_OS schema v1. SSOT de datos estructurados. entity separa personal / business.

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  horizon TEXT NOT NULL DEFAULT 'year' CHECK (horizon IN ('year','quarter','month')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','done','dropped')),
  due_date TEXT,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','blocked','paused','done')),
  entity TEXT NOT NULL DEFAULT 'personal' CHECK (entity IN ('personal','business')),
  area TEXT,
  goal_id INTEGER REFERENCES goals(id),
  next_milestone TEXT,
  due_date TEXT,
  tags TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  notes TEXT,
  status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','doing','blocked','done','dropped')),
  priority INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 4),
  project_id INTEGER REFERENCES projects(id),
  entity TEXT NOT NULL DEFAULT 'personal' CHECK (entity IN ('personal','business')),
  due_date TEXT,
  scheduled_date TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  recurrence TEXT CHECK (recurrence IN ('daily','weekly','biweekly','monthly','quarterly','yearly')),
  tags TEXT,
  start_time TEXT,           -- HH:MM: bloque de tiempo dentro del día (scheduled_date)
  est_minutes INTEGER,       -- duración estimada
  parent_id INTEGER REFERENCES tasks(id),  -- subtarea de otra tarea
  someday INTEGER NOT NULL DEFAULT 0,      -- «algún día»: guardada sin fecha ni presión
  actual_minutes INTEGER NOT NULL DEFAULT 0,  -- tiempo real enfocado (cronómetro)
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  done_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_date);

CREATE TABLE IF NOT EXISTS habits (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  cadence TEXT NOT NULL DEFAULT 'daily',
  active INTEGER NOT NULL DEFAULT 1,
  project_id INTEGER REFERENCES projects(id)  -- hábito ligado a un proyecto (p. ej. «Practicar inglés» ↔ «Aprender inglés»)
);
CREATE TABLE IF NOT EXISTS habit_log (
  habit_id INTEGER NOT NULL REFERENCES habits(id),
  day TEXT NOT NULL,
  PRIMARY KEY (habit_id, day)
);

-- Planes de trabajo: cursos (p. ej. Coursera), proyectos y metas con etapas, ítems y cronograma
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'curso' CHECK (kind IN ('curso','proyecto','meta')),
  source_url TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','done')),
  start_date TEXT,
  end_date TEXT,
  hours_per_week REAL NOT NULL DEFAULT 5,
  study_days TEXT NOT NULL DEFAULT '0,1,2,3,4',   -- días de la semana (0=lunes) en que se trabaja el plan
  project_id INTEGER REFERENCES projects(id),
  entity TEXT NOT NULL DEFAULT 'personal' CHECK (entity IN ('personal','business')),
  tags TEXT,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS plan_stages (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  start_date TEXT,
  end_date TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS plan_items (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  stage_id INTEGER NOT NULL REFERENCES plan_stages(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'leccion',
  est_minutes INTEGER NOT NULL DEFAULT 30,
  scheduled_date TEXT,
  status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','done','skipped')),
  done_at TEXT,
  url TEXT
);
CREATE INDEX IF NOT EXISTS idx_plan_items_sched ON plan_items(status, scheduled_date);

-- Preguntas a la IA (local al instante, o a Claude cuando se abre una sesión): con el contexto recuperado adjunto
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY,
  question TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'general',           -- general | plan:ID
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','answered')),
  answer TEXT,
  answered_by TEXT,                                 -- local | claude
  context TEXT,                                     -- JSON con los fragmentos recuperados
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  answered_at TEXT
);

-- Archivos de correo ya procesados (la lectura es incremental: no se reanalizan miles de mensajes en cada pasada)
CREATE TABLE IF NOT EXISTS mail_seen (path TEXT PRIMARY KEY);

-- Copia local de los eventos de Google Calendar (la rellena Claude con el conector): el portal los muestra y el planificador evita choques
CREATE TABLE IF NOT EXISTS gcal_events (
  uid TEXT PRIMARY KEY,
  calendar TEXT,
  title TEXT NOT NULL,
  start TEXT NOT NULL,   -- YYYY-MM-DD (todo el día) o YYYY-MM-DDTHH:MM hora de Bogotá
  end TEXT,
  all_day INTEGER NOT NULL DEFAULT 0,
  location TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gcal_start ON gcal_events(start);
-- Tiempo enfocado por tarea (cronómetro Pomodoro)
CREATE TABLE IF NOT EXISTS task_time (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  day TEXT NOT NULL,
  minutes INTEGER NOT NULL
);

-- Recordatorios que ya viven en Google Calendar (para que avisen aunque el PC esté apagado)
CREATE TABLE IF NOT EXISTS calendar_sync (
  key TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  title TEXT,
  date TEXT,
  synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Conocimiento: índice de las notas Markdown (la nota .md es la fuente; esto es solo índice de búsqueda)
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  tags TEXT,
  mtime REAL NOT NULL,
  body TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(title, tags, body, content='notes', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
  INSERT INTO notes_fts(rowid, title, tags, body) VALUES (new.id, new.title, new.tags, new.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, title, tags, body) VALUES ('delete', old.id, old.title, old.tags, old.body);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, title, tags, body) VALUES ('delete', old.id, old.title, old.tags, old.body);
  INSERT INTO notes_fts(rowid, title, tags, body) VALUES (new.id, new.title, new.tags, new.body);
END;

-- Vida administrativa y relaciones
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  relation TEXT,
  email TEXT,
  birthday TEXT,            -- MM-DD
  last_contact TEXT,
  notes TEXT,
  circle TEXT               -- familia | pareja | otros (familia y pareja avisan a 30 y 15 días)
);
-- Fechas que se repiten cada año (aniversarios, San Valentín, Día de la Madre…); rule = MM-DD o N:D:M
CREATE TABLE IF NOT EXISTS special_dates (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  rule TEXT NOT NULL,
  person_id INTEGER REFERENCES people(id),
  notes TEXT
);
-- Ítems importados de calendarios externos (Coursera): uid → tarea, para moverla si el calendario se mueve
CREATE TABLE IF NOT EXISTS external_items (
  uid TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  task_id INTEGER REFERENCES tasks(id),
  due TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT,
  expires_on TEXT,
  entity TEXT NOT NULL DEFAULT 'personal' CHECK (entity IN ('personal','business')),
  notes TEXT
);
CREATE TABLE IF NOT EXISTS subscriptions (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  amount REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT 'COP',
  cycle TEXT NOT NULL DEFAULT 'monthly' CHECK (cycle IN ('monthly','yearly','weekly')),
  next_date TEXT,
  entity TEXT NOT NULL DEFAULT 'personal' CHECK (entity IN ('personal','business')),
  active INTEGER NOT NULL DEFAULT 1
);

-- Finanzas (solo por importación de extractos; sin APIs bancarias)
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'bank',
  entity TEXT NOT NULL DEFAULT 'personal' CHECK (entity IN ('personal','business')),
  currency TEXT NOT NULL DEFAULT 'COP'
);
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  date TEXT NOT NULL,
  description TEXT NOT NULL,
  amount REAL NOT NULL,      -- negativo = gasto, positivo = ingreso
  category TEXT,
  notes TEXT,
  hash TEXT NOT NULL UNIQUE  -- evita duplicados al reimportar
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE TABLE IF NOT EXISTS budgets (
  category TEXT PRIMARY KEY,
  monthly_amount REAL NOT NULL
);
-- Saldos por cuenta en el tiempo (ahorros, tarjetas con saldo negativo, efectivo): de ahí sale el patrimonio
CREATE TABLE IF NOT EXISTS balances (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  date TEXT NOT NULL,
  amount REAL NOT NULL,
  UNIQUE(account_id, date)
);
-- Dispositivos emparejados con el portal (celular, tableta, otro Windows): cada uno entra con un enlace o QR de un solo uso, sin teclear el PIN
CREATE TABLE IF NOT EXISTS paired_devices (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  user_agent TEXT,
  paired_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);

-- Extractos: qué archivo ya se importó, qué meses cubre cada cuenta y los datos de cada corte de tarjeta
CREATE TABLE IF NOT EXISTS statement_files (
  hash TEXT PRIMARY KEY,
  name TEXT,
  account TEXT,
  period TEXT,
  source TEXT,
  imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS statement_periods (
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  start TEXT NOT NULL,
  end TEXT NOT NULL,
  source TEXT,
  PRIMARY KEY (account_id, start, end)
);
CREATE TABLE IF NOT EXISTS card_statements (
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  period_end TEXT NOT NULL,
  due_date TEXT,
  min_payment REAL,
  total_due REAL,
  credit_limit REAL,
  used REAL,
  pending_installments REAL,
  PRIMARY KEY (account_id, period_end)
);
CREATE TABLE IF NOT EXISTS savings_goals (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  target REAL NOT NULL,
  deadline TEXT,
  account_id INTEGER REFERENCES accounts(id),
  saved REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','done','dropped'))
);

-- Carrera / empleo (nunca se postula automáticamente)
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  site TEXT,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  role TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'saved' CHECK (status IN ('saved','applied','interview','offer','rejected','withdrawn')),
  url TEXT,
  applied_on TEXT,
  next_step TEXT,
  next_step_date TEXT,
  notes TEXT
);

-- Desarrollo
CREATE TABLE IF NOT EXISTS repos (
  path TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  branch TEXT,
  dirty INTEGER NOT NULL DEFAULT 0,
  ahead INTEGER NOT NULL DEFAULT 0,
  behind INTEGER NOT NULL DEFAULT 0,
  last_commit TEXT,
  checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'terminal',
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','processed','discarded')),
  resolved_as TEXT,
  suggestion TEXT,          -- JSON con la sugerencia local (Ollama); el usuario/Claude decide
  captured_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Correo: solo metadatos + texto recortado. El original vive en el cliente de correo (fuente de verdad).
CREATE TABLE IF NOT EXISTS emails (
  id INTEGER PRIMARY KEY,
  account TEXT NOT NULL,
  message_id TEXT NOT NULL,
  folder TEXT,
  date TEXT,
  from_addr TEXT,
  from_name TEXT,
  subject TEXT,
  snippet TEXT,
  body_text TEXT,
  category TEXT NOT NULL DEFAULT 'other',
  needs_claude INTEGER NOT NULL DEFAULT 0,
  reason TEXT,
  reviewed INTEGER NOT NULL DEFAULT 0,
  ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (account, message_id)
);
CREATE INDEX IF NOT EXISTS idx_emails_review ON emails(needs_claude, reviewed, date);
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(subject, from_addr, body_text, content='emails', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
  INSERT INTO emails_fts(rowid, subject, from_addr, body_text) VALUES (new.id, new.subject, new.from_addr, new.body_text);
END;
CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
  INSERT INTO emails_fts(emails_fts, rowid, subject, from_addr, body_text) VALUES ('delete', old.id, old.subject, old.from_addr, old.body_text);
END;

CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY,
  job TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','failed')),
  message TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs(job, started_at);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  actor TEXT NOT NULL,
  permission TEXT NOT NULL CHECK (permission IN ('READ','WRITE','LOCAL_ACTION','EXTERNAL_ACTION','SENSITIVE_ACTION')),
  action TEXT NOT NULL,
  target TEXT,
  result TEXT NOT NULL DEFAULT 'ok',
  detail TEXT
);

CREATE TABLE IF NOT EXISTS health (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  metric TEXT NOT NULL,
  value REAL,
  status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','warn','crit')),
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_health_metric ON health(metric, ts);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  level TEXT NOT NULL CHECK (level IN ('INFO','IMPORTANT','CRITICAL')),
  title TEXT NOT NULL,
  detail TEXT,
  acknowledged INTEGER NOT NULL DEFAULT 0,
  key TEXT,                              -- clave estable de la regla: evita repetir la misma alerta
  source TEXT NOT NULL DEFAULT 'system', -- system | rule (las de regla se archivan solas cuando deja de cumplirse)
  pushed INTEGER NOT NULL DEFAULT 0,     -- ya se envió al PC/celular
  link TEXT                              -- ruta del portal donde resolverla
);
