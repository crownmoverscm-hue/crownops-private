# CrownOps account setup

Each person has an individual account. There is no shared master password and no public signup that lets someone choose an owner role.

## Master owner

The first existing owner account becomes the protected **Master owner** on upgrade. Its account, email and password stay the same. For a new empty installation, the private setup code creates the first owner. Setup closes after an account exists.

Sign in with that existing account and open **Company → Team access**. Only the master owner can invite another owner or manage another owner's access. Other owner accounts can manage ordinary staff. Nobody can remove their own access or disable the master owner. Give operational managers **Dispatch** access unless they need full owner authority.

Keep the owner password and authenticator setup key in the company's password manager. Email password recovery does not remove authenticator protection; it is not an MFA recovery procedure.

## Invite the team

1. Open **Team access → Invite team member**.
2. Enter the person's name and their own email address; choose the correct role.
3. For Crew, select their existing employee record. This connects assignments and hours to the correct person.
4. Create the private link and share it only with that person. Creating a link does not send an email.
5. They open the link, choose and confirm their own password (12–200 characters), then sign in with the invited email and that password.

An invitation expires after 48 hours and works once. **Revoke link** cancels an unused invitation. Creating another invitation for the same email invalidates the previous unused link. Accounts already created for an email cannot be replaced through invitations.

## Enable email password recovery

The recovery feature is off by default and uses the existing company Gmail connection. It has a separate permission setting from client email, payments and accounting exports.

1. Merge and deploy this update.
2. Confirm the company Gmail connection under **Connections** has Gmail sending permission. Reconnect and approve the existing requested `gmail.send` scope if needed.
3. Keep the existing `CROWNOPS_SECRET_KEY`, `GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET`. The encrypted Gmail credential requires the same encryption key.
4. Confirm the app's HTTPS address is configured through `CROWNOPS_PUBLIC_URL` or `RAILWAY_PUBLIC_DOMAIN`.
5. In Railway's CrownOps service **Variables**, add `CROWNOPS_ALLOW_AUTH_EMAIL` with value `1`.
6. While `CROWNOPS_ENV` is `staging` or `test`, add `CROWNOPS_AUTH_EMAIL_TEST_ALLOWLIST` with the email of a controlled, active staff account. Use commas to include more approved testers. Do not change the app to production just to enable recovery.
7. Deploy the variables, then open **Team access → Password recovery**. It should show **Enabled for approved test email addresses**. This checks configuration; it does not prove a message reached the inbox.
8. Sign out and select **Forgot your password?**. Request recovery for that approved staff email. Check its inbox and spam folder, open the link, set and confirm a new password, then sign in again. Check that the old password and the used link no longer work. An enabled authenticator must still be required.
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
