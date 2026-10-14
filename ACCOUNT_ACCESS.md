# CrownOps account setup

Each person has an individual account. There is no shared master password and no public signup that lets someone choose an owner role.

## Master owner

The first existing owner account becomes the protected **Master owner** on upgrade. Its account, email and password stay the same. For a new empty installation, the private setup code creates the first owner. Setup closes after an account exists.

Sign in with that existing account and open **Company → Team access**. Only the master owner can invite another owner or manage another owner's access. Other owner accounts can manage ordinary staff. Nobody can remove their own access or disable the master owner. Give operational managers **Dispatch** access unless they need full owner authority.

Keep the owner password and authenticator setup key in the company's password manager. Email password recovery does not remove authenticator protection; it is not an MFA recovery procedure.

## Invite the team by email

1. Open **Team access → Invite team member**.
2. Enter the person's name and their own email address; choose the correct role. Review these details before sending.
3. For Crew, select their existing employee record. This connects assignments and hours to the correct person.
4. Select **Preview welcome email** to read the signup, device and role instructions using the reviewed details. A preview does not create an invitation or send mail.
5. Select **Send invitation email**. When Gmail confirms acceptance, the result says **Invitation email sent** and shows the recipient. Ask them to check their inbox and spam folder; provider acceptance does not prove inbox delivery.
6. They use **Create my CrownOps account**, choose and confirm a password (12–200 characters), then sign in with their invited email. They use the regular app address or installed icon on subsequent visits.

The welcome message includes instructions for Samsung/Android, iPhone/iPad, MacBook/Mac and Windows, plus first tasks for the invited role and help/password-recovery directions. It is sent as both plain text and HTML, without tracking images.

If account emails are not configured, **Send invitation email** is disabled. **Create link only** still works; it does not send mail. The result includes the private link and welcome email preview. Once sending is available, **Send this invitation by email** can email that exact unused link to the recipient stored on its invitation. It cannot redirect the message to a new address.

Sending uses the same dedicated account-email flag and staging allowlist described below. It does not enable general client email, payments or accounting exports. The owner can enable sending for a controlled test address first.

Each invitation has at most one email send attempt. Retrying the same form request returns the existing status and does not generate another link or send again. If the provider response is uncertain, CrownOps says **Email sending not confirmed**, records **Unknown**, and does not retry automatically. Check Gmail Sent and the recipient inbox before deliberately creating a replacement invitation. **Pending** or **Running** also does not mean sent. The pending-invitations list shows the latest email status. No raw signup token or message body is stored in delivery logs.

An invitation expires after 48 hours and works once. **Revoke link** cancels an unused invitation. Creating another invitation for the same email invalidates the previous unused link. Accounts already created for an email cannot be replaced through invitations.

## Enable account emails and password recovery

Account emails are off by default and use the existing company Gmail connection. It has a separate permission setting from client email, payments and accounting exports.

1. Merge and deploy this update.
2. Confirm the company Gmail connection under **Connections** has Gmail sending permission. Reconnect and approve the existing requested `gmail.send` scope if needed.
3. Keep the existing `CROWNOPS_SECRET_KEY`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET`. The encrypted Gmail credential requires the same encryption key.
4. Confirm the app's HTTPS address is configured through `CROWNOPS_PUBLIC_URL` or `RAILWAY_PUBLIC_DOMAIN`.
5. In Railway's CrownOps service **Variables**, add `CROWNOPS_ALLOW_AUTH_EMAIL` with value `1`.
6. While `CROWNOPS_ENV` is `staging` or `test`, add `CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST` with the email of a controlled, active staff account. Use commas to include more approved testers. Do not change the app to production just to enable recovery.
7. Deploy the variables, then open **Team access → Account emails**. It should show **Enabled for approved test email addresses**. This checks configuration; it does not prove a message reached the inbox.
8. First send a welcome invitation to the approved test address and check its inbox on the target device. Confirm the signup and subsequent login work. Then sign out and select **Forgot your password?**. Request recovery for that approved staff email. Check its inbox and spam folder, open the link, set and confirm a new password, then sign in again. Check that the old password and the used link no longer work. An enabled authenticator must still be required.
9. Confirm the real inbox test before giving the team this feature. While staging, add each approved active staff email to the allowlist. A separately reviewed production deployment permits recovery for all active accounts when the account-email flag remains enabled.

Do not enable `CROWNOPS_ALLOW_EXTERNAL` just for account recovery. Client email, payments and accounting permissions keep their existing separate checks. No live emails are sent by the automated tests.

## Forgotten password

Staff enter their invited email on **Forgot your password?**. The public response is the same for active, inactive and unknown accounts. An eligible active account receives a private link, valid for 30 minutes and one use. A new request replaces older unused reset links.

After a successful reset, all old sign-ins and unused reset links are revoked. The person signs in normally with their new password and their authenticator if enabled. A separate notification confirms the password change without including the password.

Requests are limited per email address and source IP. If recovery is not enabled, the screen explains that the owner can provide a private 30-minute reset link. Confirm the person's identity before sharing that link.

If Gmail does not confirm sending, CrownOps records **Unknown** and does not retry automatically. Check Gmail Sent and the recipient inbox before making another request; a provider timeout might occur after a message was accepted. If a process stops before delivery, the person can request a new link after checking their inbox.

## Remove access

In **Team access**, find the person by name and email, choose **Remove access**, and confirm. Their existing sign-ins end, unused reset links and pending invitations are canceled, and they cannot sign in or use email recovery. Employee, job and payroll records are retained. Removing access does not erase those records.

Use this when someone leaves or their account must be blocked. Disable the affected account before addressing an account takeover. Never share the master owner's login with staff.
