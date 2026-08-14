# Tallystead Email System

**Status:** Implemented foundation

**Code:** `apps/api/app/mailer.py`

## Purpose

Every email sent by Tallystead uses one standard layout. Email types provide their own subject, heading, explanatory text, and optional action button; they do not create separate HTML shells.

The shared template provides:

- A consistent Tallystead header and local-first identity.
- A responsive HTML message for modern email clients.
- A plain-text alternative for compatibility and accessibility.
- Escaping of content inserted into the HTML message.
- A standard security and local-server footer.
- Optional action-button text and URL.

The current email types are:

| Email | Trigger | Recipient | Action |
| --- | --- | --- | --- |
| SMTP configuration test | An Owner selects **Test outgoing email** | Signed-in Owner | None |
| Password reset | An active user requests password recovery | Account email address | Reset password |

Future reminders and operational notifications must use the same template.

## Household setup

An Owner configures outgoing email under **Settings → Email configuration**:

1. Enter the SMTP host and port.
2. Enter the SMTP username and password.
3. Enter the address Tallystead should use in the `From` field.
4. Select `STARTTLS`, `TLS`, or `None` for a trusted local relay.
5. Save the configuration.
6. Select **Test outgoing email**.

The test authenticates with the saved SMTP configuration and sends a real templated message to the signed-in Owner's account email. A successful test therefore verifies connection, encryption mode, authentication, sender acceptance, and message delivery by the SMTP server. Inbox placement can still depend on the household's email provider and DNS configuration.

SMTP credentials are encrypted in the local database and are write-only through the API. Saved passwords are never returned to the browser.

## Creating an email type

Import the shared builder and sender rather than constructing `EmailMessage` directly:

```python
from app.mailer import send_message, tallystead_message

message = tallystead_message(
    to_address=recipient.email,
    from_address=from_address,
    subject="A short, recognizable subject",
    heading="A clear message heading",
    paragraphs=(
        "Explain what happened and why the recipient is receiving this message.",
        "Explain what the recipient should do next, if anything.",
    ),
    action_label="Open Tallystead",
    action_url=action_url,
    preheader="A short inbox-preview summary.",
)
send_message(integration_values, message)
```

`tallystead_message` automatically creates the plain-text and HTML alternatives. Omit both `action_label` and `action_url` when the message does not need a button.

Use a small domain-specific function for each email type. That function should determine the approved recipient and message content, then call the shared builder and sender. Route handlers and workers should call the domain-specific function rather than duplicate template or SMTP logic.

## Content rules

Every new email must follow these rules:

- Use plain, calm language without judgment or urgency that is not warranted.
- State why the message was sent and what action, if any, is expected.
- Keep subjects useful without exposing private financial information on a lock screen.
- Exclude balances, transaction details, account numbers, document contents, and receipt images by default.
- Never include passwords, SMTP/IMAP credentials, access tokens, passkey data, local-AI prompts, or recovery tokens outside the required recovery link.
- Never ask a recipient to reply with a password or financial information.
- Do not add remote tracking pixels, analytics, external fonts, or third-party image URLs.
- Use the configured canonical Tallystead HTTPS address for application links.
- Give time-sensitive links a clear expiration statement.
- Make notification results reviewable in Tallystead when they concern financial state.

Content passed into the template is HTML-escaped. This protection does not replace validation of recipients, action URLs, permissions, or the underlying event.

## Delivery rules

- Password-recovery email is allowed whenever SMTP is configured because it is an account-recovery path.
- An Owner-initiated SMTP test is allowed regardless of the general notification switch.
- General reminders and operational notifications must honor `smtp_notifications_enabled`.
- A recipient must belong to the relevant household and be authorized to receive that message type.
- Sending email must not change a ledger balance, confirm a reconciliation match, approve an AI suggestion, or otherwise make a financial decision.
- SMTP failures must be handled without exposing credentials or low-level connection details to an unauthenticated user.
- Success and failure should be represented by an audit event using identifiers and a safe summary, not the message body or secrets.
- Retrying a background notification must be idempotent or use a durable delivery record before automatic retries are enabled.

## Password-reset example

Password recovery uses the standard template in `apps/api/app/password_recovery.py`. It creates a single-use token, stores only its hash, and sends the raw token inside a canonical local Tallystead link. The link expires after 30 minutes.

The request endpoint remains non-enumerating: it does not reveal whether the submitted email address belongs to an account. Reset completion revokes existing sessions for that user.

## Testing requirements

Every new email type requires automated coverage at the appropriate boundaries:

1. Verify the intended `To`, `From`, and `Subject` values.
2. Verify that both plain-text and HTML alternatives exist.
3. Verify that the important explanation and action are present in both alternatives.
4. Verify that inserted content is escaped in HTML.
5. Verify that secrets and unnecessary financial details are absent.
6. Mock SMTP delivery in API and worker tests; automated tests must not send external email.
7. Verify authorization, household scoping, notification-setting behavior, and audit events at the trigger boundary.
8. Verify safe behavior when SMTP is unavailable, rejects authentication, or refuses a sender or recipient.

Current template coverage lives in:

- `apps/api/tests/test_mailer.py` for rendering, alternatives, and escaping.
- `apps/api/tests/test_health.py` for configuration secrecy, password-recovery behavior, and the Owner SMTP-test endpoint.

Run the API checks after an email change:

```bash
cd apps/api
ruff check .
pytest
```

## Extending the standard layout

Change the shared layout only when the change benefits every outgoing email. Keep email-client compatibility in mind: use simple table-based structure, inline styles, and no JavaScript in the delivered message.

When the shared layout changes:

1. Update `apps/api/app/mailer.py`.
2. Update or add rendering tests.
3. Check both an action email and a message without an action.
4. Test the result in representative desktop and mobile email clients before treating the design as complete.

The Python builder and its rendering tests are the implementation source.
