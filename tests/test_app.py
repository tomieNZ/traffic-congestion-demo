"""Focused integration tests for the demo trust boundary."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import create_app


class TrafficDemoTests(unittest.TestCase):
    """Exercise the public HTTP behaviour a presenter relies on."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": str(self.database_path),
                "JWT_SECRET": "test-jwt-secret-with-at-least-32-bytes",
                "HMAC_SECRET": "test-hmac-secret-with-at-least-32-bytes",
                "DEMO_USERNAME": "operator",
                "DEMO_PASSWORD": "demo-password",
                "RATE_LIMIT_PER_MINUTE": 1000,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def login(self) -> str:
        response = self.client.post(
            "/auth/login",
            json={"username": "operator", "password": "demo-password"},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["access_token"]

    @staticmethod
    def headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_health_reports_model_metrics(self) -> None:
        response = self.client.get("/health")
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["model_metrics"]["training_records"], 30)

    def test_valid_signed_reading_is_classified_and_logged(self) -> None:
        token = self.login()
        scenarios = self.client.get(
            "/api/v1/demo/scenarios", headers=self.headers(token)
        ).get_json()["scenarios"]

        response = self.client.post(
            "/api/v1/traffic/evaluate",
            json=scenarios["safe"]["payload"],
            headers=self.headers(token),
        )
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["status"], "safe")
        self.assertTrue(body["models_agree"])

        history = self.client.get(
            "/api/v1/traffic/history", headers=self.headers(token)
        ).get_json()["events"]
        self.assertTrue(any(event["event_type"] == "traffic_evaluation" for event in history))

    def test_tampered_payload_is_rejected_before_classification(self) -> None:
        token = self.login()
        scenarios = self.client.get(
            "/api/v1/demo/scenarios", headers=self.headers(token)
        ).get_json()["scenarios"]
        response = self.client.post(
            "/api/v1/traffic/evaluate",
            json=scenarios["tampered"]["payload"],
            headers=self.headers(token),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "invalid_signature")

    def test_signed_but_invalid_reading_is_rejected(self) -> None:
        token = self.login()
        scenarios = self.client.get(
            "/api/v1/demo/scenarios", headers=self.headers(token)
        ).get_json()["scenarios"]
        response = self.client.post(
            "/api/v1/traffic/evaluate",
            json=scenarios["anomaly"]["payload"],
            headers=self.headers(token),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"], "invalid_reading")


if __name__ == "__main__":
    unittest.main()
