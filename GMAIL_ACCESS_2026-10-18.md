# Gmail content access correction — 0.11.2

Read-only connection tests now retrieve a sample full message from each populated inquiry label. Listing messages alone cannot prove content access, and empty labels are reported as unverified.

Import stops after the first account-wide access failure without advancing the failed feed cursor. Already committed inquiries remain intact. Google error reasons are mapped to controlled permission, administrator-policy, daily-quota, rate-limit or unavailable guidance; provider response bodies are never displayed. Rate-limit and transient failures receive bounded exponential backoff. CRM import warnings are deduplicated and marked as needing attention.

No OAuth scopes, outbound-email permissions, accounting settings or customer messages are changed by this release.
