# My Portal

My Portal replaces the My Account navigation entry. It is for signed-in staff;
the separate client portal is unchanged.

## Before the team pilot

1. Owners/managers link each staff account to the correct active employee record.
   Managers who also work on jobs need an employee link too.
2. Owners/managers configure the company payroll cycle in Payroll & hours.
3. Each employee opens My Portal → My availability and saves their actual days
   and times. An unchecked day is unavailable. A specific-date exception replaces
   the weekly hours for that date. All times use Montréal time.
4. An owner sends a sample protocol from Documents to the pilot participants.
   Recipients open it, download it, and acknowledge it if requested.

## Give Shift Away

Only this action loads the list of possible replacements. Candidates must have
an active linked account, explicitly cover the entire shift with saved
availability, have no overlapping assignment or approved leave, and retain the
outgoing employee's driver and team-lead qualifications. Expired driver licences
are excluded when a driver is needed. No availability means no suggestion.

The assigned employee selects coworkers and requests coverage. The first eligible
coworker to accept moves the request to Awaiting manager. Another owner or manager
must approve; the outgoing or incoming employee cannot approve their own change.
The current assignment remains in place until that approval. The server rechecks
availability, conflicts, qualifications and the job version before reassignment.
A changed or started job requires management attention. Travel between separate
jobs and overtime impacts still require the manager's judgement.

Employees can cancel an open request. A manager can decline one. For an immediate
emergency or a job already underway, contact management directly.

## Hours, contact information and requests

My hours shows the employee's recorded service and transport hours in the current
configured payroll cycle, through today, divided into approved and awaiting review.
It does not turn planned shift durations into payroll hours or expose pay rates.
There is no hours editor here. Ask about my hours sends a private message to
management. Existing recorded hours cannot be changed by crew through the team
hours endpoint either; authorized payroll staff retain correction access.

My information changes directory contact phone/email only. Sign-in email and role
do not change. Account security settings remain accessible there.

Requests contains shift coverage and personal time off. Time-off approvals check
overlapping service dates, including jobs that cross midnight. Messages supports
a private employee question and a manager reply; it is not a group chat.

## Owner documents and notifications

Only owners publish documents. They can write a text document or upload PDF,
DOCX, JPG, PNG or UTF-8 TXT up to 10 MB, for selected active staff accounts or all
currently active staff. The audience is fixed when published; future hires do not
automatically receive previously published documents.

Recipients can download their documents. Owners can inspect delivery readership
and acknowledgements. Opening is separate from explicitly confirming it was read.
Publish a new document for revised instructions to preserve the acknowledgement
history. Files are stored in the backed-up application database and are served only
to recipients and owners. There is no public file URL.

Alerts are in-app: My Portal shows an unread count, and an open visible app checks
for new notices every minute. These are not phone push, SMS or email notifications.
They do not depend on an email-provider connection.

## Verification

`python -m unittest discover -s tests -v` includes staff portal permission,
availability, identity, approval, document privacy and overnight time-off checks.

With Playwright and Chromium installed:
`python tests/browser/run_mobile.py staff_portal` runs the handoff through fictional
crew, replacement, manager and owner accounts, including document downloads and
320/390/768/1440-pixel layout checks. It does not message real employees.
