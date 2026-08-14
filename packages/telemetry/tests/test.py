import pytest
from fastapi import FastAPI
from telemetry import REDACTED, TelemetryConfig, instrument, operation, record, redact


def test_redact_removes_nested_secret_values() -> None:
    value = {
        "provider": "sendgrid",
        "api_key": "plaintext",
        "secret_reference": "projects/example/secrets/mail/versions/2",
        "payload": b"plaintext bytes",
        "nested": [{"authorization": "Bearer plaintext"}],
    }

    assert redact(value) == {
        "provider": "sendgrid",
        "api_key": REDACTED,
        "secret_reference": "projects/example/secrets/mail/versions/2",
        "payload": REDACTED,
        "nested": [{"authorization": REDACTED}],
    }


def test_telemetry_is_disabled_outside_managed_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("FIREKEY_TELEMETRY_ENABLED", raising=False)

    config = instrument(FastAPI(), "firekey-test")

    assert config == TelemetryConfig(
        service="firekey-test",
        project_id="",
        region="",
        environment="production",
        enabled=False,
        sample_ratio=1,
    )


def test_operation_metrics_reject_unbounded_or_sensitive_attributes() -> None:
    with pytest.raises(ValueError, match="unbounded"):
        record("agent.invoke", "succeeded", 0.1, run_id="run_one")
    with pytest.raises(ValueError, match="sensitive"):
        record("tool.execute", "succeeded", 0.1, tool=REDACTED)

    with operation("agent.invoke", {"agent": "planner", "skill": "plan_rotation"}):
        pass
