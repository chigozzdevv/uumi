from telemetry import REDACTED, redact


def test_redact_removes_nested_secret_values() -> None:
    value = {
        "provider": "sendgrid",
        "api_key": "plaintext",
        "secret_reference": "projects/example/secrets/mail/versions/2",
        "nested": [{"authorization": "Bearer plaintext"}],
    }

    assert redact(value) == {
        "provider": "sendgrid",
        "api_key": REDACTED,
        "secret_reference": "projects/example/secrets/mail/versions/2",
        "nested": [{"authorization": REDACTED}],
    }
