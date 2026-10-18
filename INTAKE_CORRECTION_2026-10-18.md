# CrownOps 0.11.1-rc1: live intake correction

The signed-in production walkthrough found that a non-URL photo reference in an incoming quote form caused HTTP 422 and stopped the remaining inquiry import. Gmail read access itself passed.

An invalid optional photo reference now leaves the inquiry usable: the original value remains in the source event and message, the clickable link stays blank and Sales receives a task to review the reference. Common empty placeholders such as N/A do not create false tasks. Other invalid inquiry fields are isolated with a transaction savepoint and retained in the review queue; unrelated messages continue. A partial lead/task insertion rolls back before the original source event is queued. Repeated imports remain duplicate-safe. Manual photo-link entry still requires a complete HTTPS URL.

The incoming-form review explains corrections and avoids offering the same invalid automatic creation again. Staff can create a corrected inquiry and attach the retained source, or dismiss an unwanted submission with a reason. Existing service details and accepted estimates are not overwritten.

The production strip now respects its hidden attribute even when the strip uses flex layout. It remains visible in a test workspace. Company email settings identify unapproved From/Reply-To addresses as proposed values and explain that the confirmed Gmail address is used until approval; no email permission was enabled.

Local validation passed: 405 server tests (including six new intake regressions), 13 JavaScript tests and five focused browser cases. Browser checks covered production/test mode at phone and desktop widths, the invalid-form review and proposed sender labels. Provider responses in automated tests were mocked; the separate production Gmail read and QuickBooks sandbox connection tests passed.

Publication, live import outcome and deployment evidence must be verified separately. The business launch checklist still requires Owner/Manager authenticator enrollment, fleet reviews, a missing employee pay rate, verification of the proposed company sender, deliberate activation of client email, and a connection to the actual production QuickBooks company before live payments. No real payment or client email was sent during these checks.
