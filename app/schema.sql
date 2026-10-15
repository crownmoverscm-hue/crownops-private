PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL COLLATE NOCASE, name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('owner','sales','dispatch','crew','accounting')),
 password_hash TEXT NOT NULL, employee_id TEXT, active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL,
 expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invitations (
 token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
 employee_id TEXT, expires_at TEXT NOT NULL, used_at TEXT, created_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (ip_hash TEXT, attempted_at TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports (
 id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
 created_at TEXT NOT NULL, report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_rows (
 id INTEGER PRIMARY KEY AUTOINCREMENT, import_id TEXT NOT NULL REFERENCES imports(id),
 sheet TEXT NOT NULL, row_number INTEGER NOT NULL, payload_json TEXT NOT NULL,
 disposition TEXT NOT NULL, UNIQUE(import_id,sheet,row_number)
);
CREATE TABLE IF NOT EXISTS leads (
 id TEXT PRIMARY KEY, external_id TEXT UNIQUE, client_name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '', move_date TEXT NOT NULL DEFAULT '', pickup TEXT NOT NULL DEFAULT '',
 dropoff TEXT NOT NULL DEFAULT '', home_size TEXT NOT NULL DEFAULT '', boxes TEXT NOT NULL DEFAULT '',
 pickup_access TEXT NOT NULL DEFAULT '', dropoff_access TEXT NOT NULL DEFAULT '',
 pickup_walk TEXT NOT NULL DEFAULT '', dropoff_walk TEXT NOT NULL DEFAULT '',
 inventory TEXT NOT NULL DEFAULT '', heavy_items TEXT NOT NULL DEFAULT '',
 photo_link TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'English',
 source TEXT NOT NULL DEFAULT 'Manual', lead_type TEXT NOT NULL DEFAULT 'Form',
 stage TEXT NOT NULL DEFAULT 'New Lead', assigned_to TEXT NOT NULL DEFAULT '',
 priority TEXT NOT NULL DEFAULT 'Normal', notes TEXT NOT NULL DEFAULT '',
 archived_import INTEGER NOT NULL DEFAULT 0, notifications_suppressed INTEGER NOT NULL DEFAULT 1,
 raw_json TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS leads_date ON leads(move_date);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, legacy_id TEXT UNIQUE, lead_id TEXT REFERENCES leads(id), title TEXT NOT NULL,
 due_date TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Open',
 historical INTEGER NOT NULL DEFAULT 0, raw_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calls (
 id TEXT PRIMARY KEY, legacy_id TEXT UNIQUE, lead_id TEXT REFERENCES leads(id),
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), kind TEXT NOT NULL,
 subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'English',
 author TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employees (
 id TEXT PRIMARY KEY, external_id TEXT UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, driver_verified INTEGER NOT NULL DEFAULT 0,
 hourly_cents INTEGER NOT NULL DEFAULT 0, raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS vehicles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0,
 notes TEXT NOT NULL DEFAULT '', color_hex TEXT NOT NULL DEFAULT '#3B82F6',
 display_order INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS pricing_versions (
 id TEXT PRIMARY KEY, label TEXT NOT NULL, rates_json TEXT NOT NULL, settings_json TEXT NOT NULL,
 approved INTEGER NOT NULL DEFAULT 0, approved_by TEXT, approved_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), revision INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'Draft', inputs_json TEXT NOT NULL, result_json TEXT NOT NULL,
 pricing_id TEXT NOT NULL REFERENCES pricing_versions(id),
 lead_snapshot_json TEXT NOT NULL, lead_version INTEGER NOT NULL,
 approved_by TEXT, approved_at TEXT, acceptance_ref TEXT, accepted_at TEXT,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(lead_id,revision)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), quote_id TEXT REFERENCES quotes(id),
 title TEXT NOT NULL, phase TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'Tentative', stops_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
 capacity_reviewed INTEGER NOT NULL DEFAULT 0, exception_reason TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(quote_id,phase)
);
CREATE TABLE IF NOT EXISTS assignments (
 job_id TEXT NOT NULL REFERENCES jobs(id), resource_type TEXT NOT NULL CHECK(resource_type IN ('employee','vehicle')),
 resource_id TEXT NOT NULL, PRIMARY KEY(job_id,resource_type,resource_id)
);
CREATE INDEX IF NOT EXISTS assignment_resource ON assignments(resource_type,resource_id);
CREATE INDEX IF NOT EXISTS jobs_lead ON jobs(lead_id);
CREATE INDEX IF NOT EXISTS messages_lead_time ON messages(lead_id,created_at);
CREATE INDEX IF NOT EXISTS tasks_lead_status ON tasks(lead_id,status);
CREATE INDEX IF NOT EXISTS tasks_owner_due ON tasks(owner,status,due_date);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS login_attempts_time ON login_attempts(attempted_at);
CREATE TABLE IF NOT EXISTS payments (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), amount_cents INTEGER NOT NULL,
 method TEXT NOT NULL, purpose TEXT NOT NULL, reference TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
 reverses_id TEXT UNIQUE REFERENCES payments(id), recorded_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS times (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), employee_id TEXT NOT NULL REFERENCES employees(id),
 service_minutes INTEGER NOT NULL, transport_minutes INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'Submitted', submitted_by TEXT NOT NULL, approved_by TEXT,
 created_at TEXT NOT NULL, UNIQUE(job_id,employee_id)
);
CREATE TABLE IF NOT EXISTS inventory (
 id TEXT PRIMARY KEY, external_id TEXT UNIQUE, name TEXT NOT NULL, category TEXT NOT NULL,
 quantity REAL NOT NULL, minimum REAL NOT NULL, sell_cents INTEGER NOT NULL DEFAULT 0,
 cost_cents INTEGER NOT NULL DEFAULT 0, location TEXT NOT NULL DEFAULT 'Warehouse',
 raw_json TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS stock_movements (
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES inventory(id), quantity REAL NOT NULL,
 reason TEXT NOT NULL, job_id TEXT REFERENCES jobs(id), author TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_events (
 id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL,
 object_id TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Unsent', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_events (
 event_id TEXT PRIMARY KEY, body_hash TEXT NOT NULL, lead_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_runs (
 id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, user_id TEXT NOT NULL, lead_id TEXT,
 model TEXT NOT NULL, status TEXT NOT NULL, reserved_tokens INTEGER NOT NULL DEFAULT 0,
 input_tokens INTEGER NOT NULL DEFAULT 0, cached_tokens INTEGER NOT NULL DEFAULT 0,
 output_tokens INTEGER NOT NULL DEFAULT 0, result_json TEXT, error TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
 object_type TEXT NOT NULL, object_id TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);

-- v0.2: additive pilot schema. Accepted quotes and employee hours are not changed.
CREATE TABLE IF NOT EXISTS job_invoices (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 number TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'Draft',
 terms_json TEXT NOT NULL, actual_json TEXT NOT NULL DEFAULT '{}',
 revision INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
 terms_approved_by TEXT NOT NULL, terms_approved_at TEXT NOT NULL,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoice_revisions (
 invoice_id TEXT NOT NULL REFERENCES job_invoices(id), revision INTEGER NOT NULL,
 document_json TEXT NOT NULL, issued_by TEXT NOT NULL, issued_at TEXT NOT NULL,
 PRIMARY KEY(invoice_id,revision)
);
CREATE TABLE IF NOT EXISTS invoice_payment_allocations (
 id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES job_invoices(id),
 payment_id TEXT NOT NULL REFERENCES payments(id), amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 allocated_by TEXT NOT NULL, created_at TEXT NOT NULL
);
