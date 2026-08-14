from email.message import EmailMessage

from app.mailer import tallystead_message


def test_standard_email_has_plain_text_and_branded_html() -> None:
    message = tallystead_message(
        to_address="owner@example.com",
        from_address="tallystead@example.com",
        subject="Tallystead test",
        heading="Outgoing email is ready",
        paragraphs=("The configuration works.",),
        action_label="Open Tallystead",
        action_url="https://tallystead.local/settings",
    )

    assert isinstance(message, EmailMessage)
    assert message["To"] == "owner@example.com"
    assert message.is_multipart()
    plain, branded = message.get_payload()
    assert "Open Tallystead: https://tallystead.local/settings" in plain.get_content()
    assert "Household finances, kept local" in branded.get_content()
    assert "background:#176f5b" in branded.get_content()


def test_standard_email_escapes_user_controlled_content() -> None:
    message = tallystead_message(
        to_address="owner@example.com",
        from_address="tallystead@example.com",
        subject="Tallystead test",
        heading="<script>alert(1)</script>",
        paragraphs=("A & B",),
    )

    html_part = message.get_payload()[1].get_content()
    assert "<script>" not in html_part
    assert "&lt;script&gt;" in html_part
    assert "A &amp; B" in html_part
