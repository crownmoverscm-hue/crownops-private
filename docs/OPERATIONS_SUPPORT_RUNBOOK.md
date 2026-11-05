# CrownOps — Operations Support Runbook

This runbook covers each company installation separately. Every finding below comes from `manage.py ops-check` (or `GET /api/ops/status` for Owners and Managers).

**Exit codes:**

| Code | Meaning | Action |
|---|---|---|
| 0 | OK | None |
| 1 | Warning | Review within one business day |
| 2 | Critical | Alert immediately |

Reports contain codes, counts and ages only. **Never** paste customer data, tokens, secret values or backup archives into tickets or chat.

## Schedule

| Task | Frequency | Command |
|---|---|---|
| Backup with restore test | Daily, outside business hours | `manage.py --db "$CROWNOPS_DB" backup-run --keep 14`, or `CROWNOPS_BACKUP_HOUR` in the app process |
| Status check | Every 5–15 minutes | `manage.py --db "$CROWNOPS_DB" ops-check` |
| Liveness | Every minute | `GET /api/health` (expect HTTP 200) |
| Offsite copy of the newest archive | Daily, after the backup | `CROWNOPS_BACKUP_OFFSITE_COMMAND` (runs automatically after each verified backup) |
| Restore drill into a separate environment | Monthly | `restore`, `db-status`, `ops-check`, owner sign-in |

## Findings and responses

| Code | Level | What it means | First response | Escalate to |
|---|---|---|---|---|
| `database_missing` / `database_unavailable` | Critical | The database cannot be read. | Check the volume mount and permissions. Do **not** create a new empty database. | Operator, then owner |
| `database_integrity` | Critical | SQLite `quick_check` failed. | Stop writes (stop the service). Restore the latest verified backup into a **new** location (deployment runbook, section 19). | Operator, then owner |
| `schema` | Critical | Migrations are pending, the schema is unexpected, or the database is newer than the release. | Check the deployed version (`/api/health`) against `db-status`. Never downgrade. Restore the pre-upgrade backup if needed. | Engineering |
| `backup_failed` | Critical | The last scheduled backup failed (the message gives the reason, for example a missing upload). | Fix the cause, then rerun `backup-run`. Nothing partial was published. | Operator |
| `backup_stale` | Critical | No successful backup within `CROWNOPS_BACKUP_MAX_AGE_HOURS`. | Check the scheduler, then rerun `backup-run`. | Operator |
| `backup_missing` | Warning | No backup has ever succeeded. | Run `backup-run` and schedule it. | Operator |
| `disk_low` | Warning or critical | Free space on the database, upload or backup volume is below the configured threshold. | Expand the volume or move old archives offsite. Never delete the database or uploads. | Operator |
| `background_job_failing` | Critical | A background job (drafts, intake, connections, estimates or receipts) has failed `CROWNOPS_JOB_FAILURE_ALERT` times in a row. | Check the provider connection under Connections and the application log (messages there contain no customer data). The job keeps retrying. | Operator, then engineering |
| `secret_key_mismatch` | Critical | Stored MFA secrets or provider tokens cannot be decrypted with the configured `CROWNOPS_SECRET_KEY`. MFA users cannot sign in. | Restore the original key from the secret store. If it is lost: `manage.py reset-mfa` per user (verify identity first), then reconnect each provider. | Operator, then owner |
| `offsite_copy_failed` | Critical | The offsite copy command failed for the newest archive. The local backup is intact. | Check storage credentials, quota and network, then rerun `backup-run`. | Operator |
| `offsite_not_configured` | Warning (production) | No offsite copy is configured. | Set `CROWNOPS_BACKUP_OFFSITE_COMMAND`. | Operator |
| `backup_unencrypted` | Warning (production) | The last backup is not encrypted. | Set `CROWNOPS_BACKUP_KEY` and keep an offline copy. | Operator |
| `messages_unconfirmed` | Warning | A client email ended in Unknown or Failed. | In Inbox → Sent, check the provider's sent folder before any resend. Unknown outcomes are reconciled, never blindly retried. | Office manager |
| `integration_reconciliation` | Warning | A QuickBooks or Square operation needs reconciliation. | Owner or Manager reconciles it in the invoice or payment screens. No automatic retry. | Accounting |

## Logs

The application logs to stdout. Log messages contain no customer records, tokens or financial details: background-job failures log a fixed message, and only the exception type is recorded.

Keep logs in the hosting provider's private log store, with retention that matches the customer agreement.

## Support boundaries

- **Live provider status is not verified.** Email delivery, QuickBooks and Square have not been tested live for commercial installations. Treat their failures as integration issues for that customer's accounts.
- **Crown Movers' installation** is operated separately. Its identity and production settings change only with Jonathan's authorization.
