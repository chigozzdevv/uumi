from demo import create_app
from fastapi.testclient import TestClient


def test_demo_reports_a_non_secret_credential_fingerprint() -> None:
    key = "re_example_demo_key_1234567890"

    with TestClient(create_app(key)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["credential_fingerprint"]
    assert key not in response.text
