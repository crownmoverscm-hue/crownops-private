-- v0.4: additive migration. No source records or live app data are replaced.
CREATE TABLE IF NOT EXISTS detail_requests (
 id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),request_key TEXT NOT NULL,request_json TEXT NOT NULL,
 fields_json TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',mail_id TEXT REFERENCES outbound_messages(id),
 status TEXT NOT NULL DEFAULT 'Open',created_by TEXT NOT NULL,created_at TEXT NOT NULL,submitted_at TEXT,reviewed_at TEXT,
 UNIQUE(lead_id,request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS detail_requests_open ON detail_requests(lead_id) WHERE status='Open';
CREATE TABLE IF NOT EXISTS portal_detail_drafts (
 lead_id TEXT PRIMARY KEY REFERENCES leads(id),base_version INTEGER NOT NULL,fields_json TEXT NOT NULL,
 revision INTEGER NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quote_views (
 id TEXT PRIMARY KEY,quote_id TEXT NOT NULL REFERENCES quotes(id),lead_id TEXT NOT NULL REFERENCES leads(id),
 session_hash TEXT NOT NULL,time_bucket INTEGER NOT NULL,viewed_at TEXT NOT NULL,
 UNIQUE(quote_id,session_hash,time_bucket)
);
CREATE INDEX IF NOT EXISTS quote_views_quote ON quote_views(quote_id,viewed_at);
CREATE TABLE IF NOT EXISTS portal_invites (
 token_hash TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), email TEXT NOT NULL,
 expires_at TEXT NOT NULL, used_at TEXT, revoked INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portal_sessions (
 token_hash TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), email TEXT NOT NULL, csrf TEXT NOT NULL,
 expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_changes (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), payload_json TEXT NOT NULL,
 base_version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT NOT NULL,
 reviewed_by TEXT, reviewed_at TEXT, review_note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS attachments (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), job_id TEXT REFERENCES jobs(id),
 category TEXT NOT NULL, original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE, mime TEXT NOT NULL,
 size INTEGER NOT NULL, sha256 TEXT NOT NULL, scan_status TEXT NOT NULL DEFAULT 'Quarantined',
 author TEXT NOT NULL, created_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS attachments_lead_live ON attachments(lead_id,deleted_at);
CREATE TABLE IF NOT EXISTS media_releases (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), attachment_id TEXT REFERENCES attachments(id),
 customer_name TEXT NOT NULL, email TEXT NOT NULL, release_version TEXT NOT NULL, release_text TEXT NOT NULL,
 channels_json TEXT NOT NULL, compensation_cents INTEGER NOT NULL DEFAULT 5000,
 status TEXT NOT NULL DEFAULT 'Consented', signed_at TEXT NOT NULL, withdrawn_at TEXT, reviewed_by TEXT, reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS project_credits (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), program TEXT NOT NULL,
 release_id TEXT NOT NULL REFERENCES media_releases(id), invoice_id TEXT REFERENCES job_invoices(id),
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), state TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(lead_id,program)
);
CREATE TABLE IF NOT EXISTS cases (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), job_id TEXT REFERENCES jobs(id),
 type TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Open',
 owner TEXT NOT NULL DEFAULT '', resolution TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fleet_profiles (
 vehicle_id TEXT PRIMARY KEY REFERENCES vehicles(id), plate TEXT NOT NULL DEFAULT '', vin TEXT NOT NULL DEFAULT '',
 model TEXT NOT NULL DEFAULT '', payload_kg REAL, gvwr_kg REAL, cargo_length_ft REAL,
 odometer_km REAL NOT NULL DEFAULT 0, next_service_km REAL, next_service_date TEXT NOT NULL DEFAULT '',
 inspection_due TEXT NOT NULL DEFAULT '', inspection_interval_hours INTEGER NOT NULL DEFAULT 24,
 check_required INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fleet_documents (
 id TEXT PRIMARY KEY,
 vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
 category TEXT NOT NULL,
 original_name TEXT NOT NULL,
 stored_name TEXT NOT NULL UNIQUE,
 mime TEXT NOT NULL,
 size INTEGER NOT NULL,
 sha256 TEXT NOT NULL,
 scan_status TEXT NOT NULL DEFAULT 'Quarantined',
 author TEXT NOT NULL,
 created_at TEXT NOT NULL,
 deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS fleet_documents_vehicle ON fleet_documents(vehicle_id,created_at);

CREATE TABLE IF NOT EXISTS inspections (
 id TEXT PRIMARY KEY, vehicle_id TEXT NOT NULL REFERENCES vehicles(id), job_id TEXT REFERENCES jobs(id),
 checked_by TEXT NOT NULL, checked_at TEXT NOT NULL, odometer_km REAL NOT NULL,
 checklist_json TEXT NOT NULL, severity TEXT NOT NULL, notes TEXT NOT NULL, cleared_by TEXT,
 cleared_at TEXT, clearance_note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS maintenance (
 id TEXT PRIMARY KEY, vehicle_id TEXT NOT NULL REFERENCES vehicles(id), title TEXT NOT NULL,
 due_date TEXT NOT NULL DEFAULT '', due_km REAL, status TEXT NOT NULL DEFAULT 'Open', cost_cents INTEGER NOT NULL DEFAULT 0,
 notes TEXT NOT NULL DEFAULT '', completed_at TEXT, author TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
 vehicle_id TEXT PRIMARY KEY REFERENCES vehicles(id), latitude REAL NOT NULL, longitude REAL NOT NULL,
 observed_at TEXT NOT NULL, source TEXT NOT NULL, received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_profiles (
 employee_id TEXT PRIMARY KEY REFERENCES employees(id), license_expiry TEXT NOT NULL DEFAULT '',
 unavailable_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paid_work_terms (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), paid_transport_minutes INTEGER,
 basis TEXT NOT NULL, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hos_dispatch_verifications (
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES jobs(id),
 job_version INTEGER NOT NULL,
 driver_id TEXT NOT NULL REFERENCES employees(id),
 vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
 cycle INTEGER NOT NULL CHECK(cycle IN (1,2)),
 cycle_on_duty_hours REAL NOT NULL,
 on_duty_since_last_24h_off REAL NOT NULL,
 consecutive_off_duty_before_shift REAL NOT NULL,
 had_24h_off_in_prev_14d INTEGER NOT NULL DEFAULT 0,
 dce_log_checked INTEGER NOT NULL DEFAULT 0,
 daily_off_duty_plan_checked INTEGER NOT NULL DEFAULT 0,
 elapsed_window_checked INTEGER NOT NULL DEFAULT 0,
 projected_on_duty_hours REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'Verified',
 note TEXT NOT NULL,
 verified_by TEXT NOT NULL,
 verified_at TEXT NOT NULL,
 UNIQUE(job_id,job_version,driver_id,vehicle_id)
);
CREATE INDEX IF NOT EXISTS hos_dispatch_job_version ON hos_dispatch_verifications(job_id,job_version);
CREATE TABLE IF NOT EXISTS payroll_drafts (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), employee_id TEXT NOT NULL REFERENCES employees(id),
 invoice_id TEXT NOT NULL REFERENCES job_invoices(id), invoice_revision INTEGER NOT NULL,
 service_minutes INTEGER NOT NULL, transport_minutes INTEGER, break_minutes INTEGER NOT NULL DEFAULT 0,
 extra_minutes INTEGER NOT NULL DEFAULT 0, hourly_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Needs review',
 note TEXT NOT NULL DEFAULT '', approved_by TEXT, approved_at TEXT, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, UNIQUE(job_id,employee_id,invoice_revision)
);
CREATE TABLE IF NOT EXISTS daily_rates (
 id TEXT PRIMARY KEY, service_date TEXT NOT NULL, movers INTEGER NOT NULL,
 hourly_cents INTEGER NOT NULL, version INTEGER NOT NULL, approved_by TEXT NOT NULL,
 note TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(service_date,movers,version)
);
CREATE TABLE IF NOT EXISTS source_promotions (
 source_key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), lead_id TEXT NOT NULL REFERENCES leads(id),
 approved_by TEXT NOT NULL, approved_at TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbound_messages (
 id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, lead_id TEXT NOT NULL REFERENCES leads(id), job_id TEXT REFERENCES jobs(id),
 subject TEXT NOT NULL, body TEXT NOT NULL, recipient TEXT NOT NULL, category TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'Draft', provider_id TEXT, error TEXT, source_version INTEGER,
 approved_by TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT
);
CREATE TABLE IF NOT EXISTS bridge_events (
 event_id TEXT PRIMARY KEY, body_hash TEXT NOT NULL, kind TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_states (
 state_hash TEXT PRIMARY KEY, provider TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
 expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS oauth_credentials (
 provider TEXT PRIMARY KEY, ciphertext TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_records (
 provider TEXT NOT NULL, kind TEXT NOT NULL, object_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 external_id TEXT NOT NULL, sync_token TEXT NOT NULL DEFAULT '', amount_cents INTEGER, updated_at TEXT NOT NULL,
 PRIMARY KEY(provider,kind,object_id,revision)
);
CREATE TABLE IF NOT EXISTS provider_operations (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, object_id TEXT NOT NULL, kind TEXT NOT NULL,
 status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}', error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_links (
 id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES job_invoices(id), revision INTEGER NOT NULL,
 amount_cents INTEGER NOT NULL, provider TEXT NOT NULL, order_id TEXT UNIQUE, link_id TEXT,
 url TEXT, status TEXT NOT NULL DEFAULT 'Creating', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS field_collections (
 id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES job_invoices(id), payment_id TEXT NOT NULL UNIQUE REFERENCES payments(id),
 request_id TEXT UNIQUE NOT NULL, received_by TEXT NOT NULL, handover_status TEXT NOT NULL DEFAULT 'Awaiting verification',
 verified_by TEXT, verified_at TEXT, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS schedule_days (
 day TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('Open','Fully booked')),
 reason TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intake_drafts (
 lead_id TEXT PRIMARY KEY REFERENCES leads(id), inputs_json TEXT NOT NULL DEFAULT '{}',
 missing_json TEXT NOT NULL DEFAULT '[]', quote_id TEXT REFERENCES quotes(id),
 status TEXT NOT NULL DEFAULT 'Needs details', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_callbacks (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), request_key TEXT NOT NULL,
 phone TEXT NOT NULL, preferred_time TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'Requested', created_at TEXT NOT NULL, UNIQUE(lead_id,request_key)
);
CREATE TABLE IF NOT EXISTS account_resets (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS account_recovery_attempts (
 purpose TEXT NOT NULL, ip_hash TEXT NOT NULL, email_hash TEXT NOT NULL DEFAULT '', attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recovery_attempts_time ON account_recovery_attempts(attempted_at);
CREATE TABLE IF NOT EXISTS auth_factors (
 user_id TEXT PRIMARY KEY REFERENCES users(id), secret_ciphertext TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0, last_step INTEGER NOT NULL DEFAULT -1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_release_terms (release_id TEXT PRIMARY KEY REFERENCES media_releases(id), snapshot_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS integration_checks (provider TEXT PRIMARY KEY, checked_at TEXT NOT NULL, checked_by TEXT NOT NULL, note TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS payroll_adjustments (
 id TEXT PRIMARY KEY, draft_id TEXT NOT NULL UNIQUE REFERENCES payroll_drafts(id),
 base_draft_id TEXT NOT NULL REFERENCES payroll_drafts(id), job_id TEXT NOT NULL REFERENCES jobs(id),
 employee_id TEXT NOT NULL REFERENCES employees(id), effective_date TEXT NOT NULL,
 service_delta INTEGER NOT NULL, transport_delta INTEGER NOT NULL, break_delta INTEGER NOT NULL,
 extra_delta INTEGER NOT NULL, pay_delta_cents INTEGER NOT NULL,
 note TEXT NOT NULL, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deposit_links (
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id), quote_id TEXT NOT NULL REFERENCES quotes(id),
 amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Creating', link_id TEXT, order_id TEXT UNIQUE,
 url TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);

-- v0.5 review-build features: time off, bulk crew hours, annual rates, materials and approval documents.
CREATE TABLE IF NOT EXISTS time_off_requests (
 id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employees(id), start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Approved','Declined','Cancelled')),
 note TEXT NOT NULL DEFAULT '', requested_by TEXT NOT NULL, requested_at TEXT NOT NULL,
 reviewed_by TEXT, reviewed_at TEXT, review_note TEXT NOT NULL DEFAULT '', cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS time_off_employee_dates ON time_off_requests(employee_id,start_date,end_date,status);

CREATE TABLE IF NOT EXISTS manager_notifications (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, object_id TEXT NOT NULL, message TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'Unread' CHECK(status IN ('Unread','Read')), created_at TEXT NOT NULL, read_at TEXT
);

CREATE TABLE IF NOT EXISTS annual_rate_periods (
 id TEXT PRIMARY KEY, label TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 rate_2_cents INTEGER NOT NULL, rate_3_cents INTEGER NOT NULL, rate_4_cents INTEGER NOT NULL,
 rate_5_cents INTEGER NOT NULL, rate_6_cents INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 version INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '', approved_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS annual_rate_dates ON annual_rate_periods(start_date,end_date,active);

CREATE TABLE IF NOT EXISTS material_catalog (
 id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Moving material',
 sell_cents INTEGER NOT NULL DEFAULT 0, cost_cents INTEGER NOT NULL DEFAULT 0, quantity REAL NOT NULL DEFAULT 0,
 minimum REAL NOT NULL DEFAULT 0, location TEXT NOT NULL DEFAULT 'Warehouse', taxable INTEGER NOT NULL DEFAULT 1,
 active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_templates (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 body TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, manager_only INTEGER NOT NULL DEFAULT 0,
 note TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_approvals (
 id TEXT PRIMARY KEY, template_id TEXT NOT NULL REFERENCES document_templates(id), template_version INTEGER NOT NULL,
 lead_id TEXT NOT NULL REFERENCES leads(id), job_id TEXT REFERENCES jobs(id), rendered_body TEXT NOT NULL,
 recipient TEXT NOT NULL, delivery_method TEXT NOT NULL CHECK(delivery_method IN ('Email','Text','Copy link')),
 status TEXT NOT NULL DEFAULT 'Prepared' CHECK(status IN ('Prepared','Approved','Declined','Expired','Cancelled')),
 token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, sent_at TEXT, approved_at TEXT, declined_at TEXT,
 signed_name TEXT NOT NULL DEFAULT '', response_note TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS document_approvals_job ON document_approvals(job_id,status);

-- v0.5.1 UX review: employee directory and company communication defaults.
CREATE TABLE IF NOT EXISTS employee_directory_profiles (
 employee_id TEXT PRIMARY KEY REFERENCES employees(id),
 email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', home_address TEXT NOT NULL DEFAULT '',
 emergency_name TEXT NOT NULL DEFAULT '', emergency_phone TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL DEFAULT '',
 team_lead INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', license_original_name TEXT NOT NULL DEFAULT '', license_stored_name TEXT NOT NULL DEFAULT '',
 license_mime TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inbox_claims (
 message_id TEXT PRIMARY KEY REFERENCES messages(id), claimed_by TEXT NOT NULL REFERENCES users(id), claimed_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS action_assignments (
 action_type TEXT NOT NULL,
 action_id TEXT NOT NULL,
 assigned_to TEXT NOT NULL REFERENCES users(id),
 assigned_by TEXT NOT NULL REFERENCES users(id),
 assigned_at TEXT NOT NULL,
 PRIMARY KEY(action_type,action_id)
);
CREATE TABLE IF NOT EXISTS action_outcomes (
 id TEXT PRIMARY KEY,
 action_type TEXT NOT NULL,
 action_id TEXT NOT NULL,
 lead_id TEXT,
 outcome TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 followup_task_id TEXT,
 followup_date TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS action_outcomes_action ON action_outcomes(action_type,action_id,created_at);
CREATE INDEX IF NOT EXISTS action_outcomes_lead ON action_outcomes(lead_id,created_at);

-- Employee incident reports retain immutable submissions and account-based review history.
CREATE TABLE IF NOT EXISTS incident_reports (
 id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL,
 location TEXT NOT NULL DEFAULT '', occurred_at TEXT NOT NULL, job_id TEXT REFERENCES jobs(id), lead_id TEXT REFERENCES leads(id),
 vehicle_id TEXT REFERENCES vehicles(id), other_vehicle TEXT NOT NULL DEFAULT '',
 reported_by TEXT NOT NULL, submitted_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Submitted',
 reviewed_by TEXT, reviewed_at TEXT, verified_by TEXT, verified_at TEXT, completed_by TEXT, completed_at TEXT,
 resolution TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
 request_key TEXT NOT NULL, payload_hash TEXT NOT NULL, legacy_case_id TEXT UNIQUE,
 UNIQUE(reported_by,request_key)
);
CREATE INDEX IF NOT EXISTS incident_submitted ON incident_reports(submitted_at DESC,id);
CREATE INDEX IF NOT EXISTS incident_reporter ON incident_reports(reported_by,submitted_at DESC);
CREATE TABLE IF NOT EXISTS incident_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL REFERENCES incident_reports(id),
 actor TEXT NOT NULL, action TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);

-- Company costs are available only through Owner / Manager expense routes.
CREATE TABLE IF NOT EXISTS expenses (
 id TEXT PRIMARY KEY, request_key TEXT NOT NULL, request_hash TEXT NOT NULL, expense_date TEXT NOT NULL, vendor TEXT NOT NULL, category TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
 subtotal_cents INTEGER NOT NULL CHECK(subtotal_cents>=0), tax_cents INTEGER NOT NULL CHECK(tax_cents>=0),
 total_cents INTEGER NOT NULL CHECK(total_cents=subtotal_cents+tax_cents AND total_cents>0),
 status TEXT NOT NULL CHECK(status IN ('Paid','Unpaid','Voided')), receipt_url TEXT NOT NULL DEFAULT '',
 job_id TEXT REFERENCES jobs(id), vehicle_id TEXT REFERENCES vehicles(id),
 version INTEGER NOT NULL DEFAULT 1, void_reason TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(created_by,request_key)
);
CREATE INDEX IF NOT EXISTS expense_date ON expenses(expense_date,status);

CREATE TABLE IF NOT EXISTS saved_media_links (
 id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),job_id TEXT REFERENCES jobs(id),
 label TEXT NOT NULL,url TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,removed_at TEXT
);
CREATE INDEX IF NOT EXISTS media_links_lead ON saved_media_links(lead_id);
CREATE TABLE IF NOT EXISTS invoice_deposit_reviews (
 invoice_id TEXT PRIMARY KEY REFERENCES job_invoices(id), fingerprint TEXT NOT NULL,
 reason TEXT NOT NULL, reviewed_by TEXT NOT NULL REFERENCES users(id), reviewed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_handoffs (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), job_version INTEGER NOT NULL,
 checklist_json TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1, updated_by TEXT REFERENCES users(id), updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_access_invites (
 token_hash TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES job_invoices(id),
 revision INTEGER NOT NULL, recipient TEXT NOT NULL, expires_at TEXT NOT NULL,
 used_at TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoice_access_sessions (
 token_hash TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES job_invoices(id),
 revision INTEGER NOT NULL, recipient TEXT NOT NULL, csrf TEXT NOT NULL, expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_change_orders (
 id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES job_invoices(id),
 request_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL,
 name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0), unit_cents INTEGER NOT NULL CHECK(unit_cents>=0),
 reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Requested', requested_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL, agreement TEXT NOT NULL DEFAULT '', reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS expense_sources (
 id TEXT PRIMARY KEY, realm_id TEXT NOT NULL, environment TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('Purchase','Bill')), external_id TEXT NOT NULL,
 source_hash TEXT NOT NULL, data_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('Review','Imported','Linked','Ignored','Changed','Unsupported')),
 expense_id TEXT UNIQUE REFERENCES expenses(id), version INTEGER NOT NULL DEFAULT 1,
 checked_at TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(realm_id,environment,kind,external_id)
);
CREATE TABLE IF NOT EXISTS wix_events (
 id TEXT PRIMARY KEY, event TEXT NOT NULL, document_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
 received_at TEXT NOT NULL, last_received_at TEXT NOT NULL,
 deliveries INTEGER NOT NULL DEFAULT 1,
 status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 service_date TEXT NOT NULL DEFAULT '', start_time TEXT NOT NULL DEFAULT '',
 lead_id TEXT REFERENCES leads(id), calendar_id TEXT,
 review_note TEXT NOT NULL DEFAULT '', reviewed_by TEXT, reviewed_at TEXT,
 UNIQUE(event,document_id,fingerprint)
);
CREATE INDEX IF NOT EXISTS wix_events_document ON wix_events(event,document_id);
CREATE INDEX IF NOT EXISTS wix_events_received ON wix_events(received_at);

-- Shared Inbox: a conversation handler is independent of the service owner.
CREATE TABLE IF NOT EXISTS service_conversations (
 lead_id TEXT PRIMARY KEY REFERENCES leads(id),handler_id TEXT REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'Needs reply' CHECK(status IN ('Needs reply','In progress','Waiting on client','Resolved')),
 remind_on TEXT NOT NULL DEFAULT '',seen_signal TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_entries (
 id TEXT PRIMARY KEY,lead_id TEXT NOT NULL REFERENCES leads(id),request_key TEXT NOT NULL,
 kind TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,actor TEXT NOT NULL,
 visibility TEXT NOT NULL CHECK(visibility IN ('staff','client')),
 direction TEXT NOT NULL CHECK(direction IN ('staff','client')),
 created_at TEXT NOT NULL,UNIQUE(lead_id,request_key)
);
CREATE INDEX IF NOT EXISTS conversation_entries_service ON conversation_entries(lead_id,created_at);
-- Staff self-service records. Existing jobs, time entries and identities remain authoritative.
CREATE TABLE IF NOT EXISTS staff_availability (
 employee_id TEXT PRIMARY KEY REFERENCES employees(id), payload_json TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_cover_requests (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), job_version INTEGER NOT NULL,
 source_employee TEXT NOT NULL REFERENCES employees(id), target_employee TEXT REFERENCES employees(id),
 requested_by TEXT NOT NULL REFERENCES users(id), message TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'Awaiting coworker', created_at TEXT NOT NULL,
 reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT, review_note TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS staff_one_open_cover ON staff_cover_requests(job_id,source_employee)
 WHERE status IN ('Awaiting coworker','Awaiting manager');
CREATE TABLE IF NOT EXISTS staff_cover_recipients (
 request_id TEXT NOT NULL REFERENCES staff_cover_requests(id), employee_id TEXT NOT NULL REFERENCES employees(id),
 PRIMARY KEY(request_id,employee_id)
);
CREATE TABLE IF NOT EXISTS staff_documents (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL DEFAULT '',
 content BLOB, requires_ack INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_document_recipients (
 document_id TEXT NOT NULL REFERENCES staff_documents(id), user_id TEXT NOT NULL REFERENCES users(id),
 read_at TEXT, ack_at TEXT, PRIMARY KEY(document_id,user_id)
);
CREATE TABLE IF NOT EXISTS staff_portal_notifications (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
 kind TEXT NOT NULL, object_id TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT
);
CREATE INDEX IF NOT EXISTS staff_notice_user ON staff_portal_notifications(user_id,read_at);
CREATE TABLE IF NOT EXISTS staff_help_requests (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), subject TEXT NOT NULL, body TEXT NOT NULL,
 reply TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, replied_at TEXT, replied_by TEXT REFERENCES users(id)
);
