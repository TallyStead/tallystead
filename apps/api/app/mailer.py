import html
import smtplib
from collections.abc import Sequence
from email.message import EmailMessage

BRAND_GREEN = "#176f5b"
INK = "#182130"
MUTED = "#667085"
SURFACE = "#f7f4ee"


def tallystead_message(
    *,
    to_address: str,
    from_address: str,
    subject: str,
    heading: str,
    paragraphs: Sequence[str],
    action_label: str | None = None,
    action_url: str | None = None,
    preheader: str | None = None,
) -> EmailMessage:
    """Build the standard Tallystead email with HTML and plain-text alternatives."""
    safe_heading = html.escape(heading)
    safe_paragraphs = [html.escape(paragraph) for paragraph in paragraphs]
    plain_lines = [heading, "", *paragraphs]
    action_html = ""
    if action_label and action_url:
        safe_label = html.escape(action_label)
        safe_url = html.escape(action_url, quote=True)
        plain_lines.extend(["", f"{action_label}: {action_url}"])
        action_html = (
            '<p style="margin:28px 0">'
            f'<a href="{safe_url}" style="background:{BRAND_GREEN};border-radius:8px;'
            "color:#ffffff;display:inline-block;font-weight:700;padding:13px 20px;"
            f'text-decoration:none">{safe_label}</a></p>'
        )
    plain_lines.extend(
        [
            "",
            "This message was sent by your household's local Tallystead server.",
            "Tallystead will never ask you to reply with a password or financial information.",
        ]
    )
    paragraph_html = "".join(
        f'<p style="color:{INK};font-size:16px;line-height:1.6;margin:0 0 16px">{value}</p>'
        for value in safe_paragraphs
    )
    safe_preheader = html.escape(preheader or paragraphs[0] if paragraphs else heading)
    html_body = f"""\
<!doctype html>
<html lang="en">
  <body style="background:{SURFACE};margin:0;padding:0">
    <span style="display:none;font-size:1px;color:{SURFACE};max-height:0;max-width:0;opacity:0;overflow:hidden">{safe_preheader}</span>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{SURFACE};padding:32px 12px">
      <tr><td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#ffffff;border:1px solid #dfe4e7;border-radius:12px;max-width:600px;overflow:hidden">
          <tr><td style="background:#173652;color:#ffffff;font-family:Arial,sans-serif;padding:22px 28px">
            <strong style="font-size:20px;letter-spacing:.2px">Tallystead</strong>
            <div style="color:#bfe6dc;font-size:12px;margin-top:4px;text-transform:uppercase;letter-spacing:1px">Household finances, kept local</div>
          </td></tr>
          <tr><td style="font-family:Arial,sans-serif;padding:32px 28px">
            <h1 style="color:{INK};font-size:26px;line-height:1.25;margin:0 0 20px">{safe_heading}</h1>
            {paragraph_html}
            {action_html}
          </td></tr>
          <tr><td style="border-top:1px solid #e6e9eb;color:{MUTED};font-family:Arial,sans-serif;font-size:12px;line-height:1.5;padding:20px 28px">
            This message was sent by your household's local Tallystead server.<br>
            Tallystead will never ask you to reply with a password or financial information.
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = to_address
    message.set_content("\n".join(plain_lines))
    message.add_alternative(html_body, subtype="html")
    return message


def send_message(values: dict, message: EmailMessage) -> None:
    host = values.get("smtp_host")
    if not host:
        raise ValueError("SMTP is not configured")
    port = int(values.get("smtp_port") or 587)
    security = values.get("smtp_security") or "starttls"
    smtp_type = smtplib.SMTP_SSL if security == "tls" else smtplib.SMTP
    with smtp_type(host, port, timeout=10) as smtp:
        if security == "starttls":
            smtp.starttls()
        username = values.get("smtp_username")
        password = values.get("smtp_password")
        if username:
            smtp.login(username, password or "")
        smtp.send_message(message)


def send_test_email(values: dict, to_address: str) -> None:
    from_address = values.get("smtp_from_address") or values.get("smtp_username")
    if not from_address:
        raise ValueError("SMTP sender address is not configured")
    message = tallystead_message(
        to_address=to_address,
        from_address=from_address,
        subject="Your Tallystead email is working",
        heading="Outgoing email is ready",
        paragraphs=(
            "Tallystead successfully connected to your outgoing email service and sent this test message.",
            "Password recovery and enabled household notifications can now use this address.",
        ),
        preheader="Your Tallystead outgoing email configuration is working.",
    )
    send_message(values, message)
