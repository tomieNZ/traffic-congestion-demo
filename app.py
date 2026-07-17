"""Flask application for the Secure Real-Time Traffic demo.

Run this file from the project directory with ``python app.py``.  The Flask
application factory also makes the API straightforward to test without
starting a real web server.
"""

from __future__ import annotations

import math
import os
import re
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

from flask import Flask, Response, g, jsonify, render_template, request

from model import CONGESTION_THRESHOLD, CongestionClassifier
from sample_data import TRAINING_ROWS, make_demo_scenarios
from security import (
    TokenError,
    create_access_token,
    decode_access_token,
    verify_payload_signature,
)
from storage import AuditLog


NUMERIC_LIMITS = {
    "distance_km": (0.01, 50.0),
    "rho_current": (0.0, 200.0),
    "rho_previous": (0.0, 200.0),
    "alpha": (0.0, 10.0),
    "estimated_drho": (0.0, 200.0),
}
REQUIRED_FIELDS = {
    "road_id",
    "distance_km",
    "rho_current",
    "rho_previous",
    "alpha",
    "estimated_drho",
    "device_id",
    "timestamp",
    "signature",
}


class ReadingValidationError(ValueError):
    """Raised when a signed payload is structurally or semantically invalid."""


def _env_int(name: str, default: int) -> int:
    """Read a positive integer from the environment with a safe fallback."""

    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def validate_reading(payload: dict[str, Any]) -> None:
    """Validate the sensor contract before the data reaches the model.

    Authentication and signatures answer *who sent this* and *whether it was
    changed*.  They do not prove that the values make sense.  This function is
    the separate domain-validation layer that rejects malformed, impossible,
    or internally inconsistent readings.
    """

    missing = sorted(REQUIRED_FIELDS - payload.keys())
    if missing:
        raise ReadingValidationError(f"Missing required fields: {', '.join(missing)}")

    for field in ("road_id", "device_id"):
        value = payload[field]
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
            raise ReadingValidationError(f"{field} must be 1-32 letters, numbers, '_' or '-'.")

    timestamp = payload["timestamp"]
    if not isinstance(timestamp, str):
        raise ReadingValidationError("timestamp must be an ISO-8601 string.")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReadingValidationError("timestamp must be a valid ISO-8601 value.") from exc

    for field, (minimum, maximum) in NUMERIC_LIMITS.items():
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReadingValidationError(f"{field} must be numeric.")
        if not math.isfinite(float(value)) or not minimum <= float(value) <= maximum:
            raise ReadingValidationError(f"{field} must be between {minimum} and {maximum}.")

    # The demo treats estimated d-rho as the change between the two density
    # readings.  A tolerance allows normal sensor rounding while still catching
    # a signed payload whose values contradict one another.
    expected_drho = float(payload["rho_current"]) - float(payload["rho_previous"])
    if abs(expected_drho - float(payload["estimated_drho"])) > 5.0:
        raise ReadingValidationError(
            "estimated_drho is inconsistent with rho_current and rho_previous."
        )


def _error(message: str, status: int, error: str = "request_rejected") -> tuple[Response, int]:
    """Keep API errors consistent so the Dashboard can render them directly."""

    return jsonify({"error": error, "message": message}), status


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    """Create and configure the demo application."""

    app = Flask(__name__)
    app.config.from_mapping(
        JWT_SECRET=os.getenv("JWT_SECRET", "local-demo-jwt-secret-change-me-32-bytes"),
        HMAC_SECRET=os.getenv("HMAC_SECRET", "local-demo-hmac-secret-change-me-32-bytes"),
        DEMO_USERNAME=os.getenv("DEMO_USERNAME", "operator"),
        DEMO_PASSWORD=os.getenv("DEMO_PASSWORD", "demo-password"),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "traffic_demo.sqlite3"),
        RATE_LIMIT_PER_MINUTE=_env_int("RATE_LIMIT_PER_MINUTE", 30),
        TOKEN_LIFETIME_MINUTES=_env_int("TOKEN_LIFETIME_MINUTES", 30),
    )
    if test_config:
        app.config.update(test_config)

    classifier = CongestionClassifier(TRAINING_ROWS)
    audit_log = AuditLog(app.config["DATABASE_PATH"])
    app.extensions["classifier"] = classifier
    app.extensions["audit_log"] = audit_log
    app.extensions["rate_limit_buckets"] = {}

    def enforce_rate_limit() -> Response | None:
        """Apply a small per-process request limit to protected endpoints.

        ponytail: an in-memory limiter is enough for a single-process classroom
        demo; use Redis or an API gateway when multiple workers or durable
        quotas matter.
        """

        client_key = request.remote_addr or "local-client"
        now = time.monotonic()
        buckets: dict[str, list[float]] = app.extensions["rate_limit_buckets"]
        timestamps = buckets.setdefault(client_key, [])
        timestamps[:] = [stamp for stamp in timestamps if now - stamp < 60]
        if len(timestamps) >= app.config["RATE_LIMIT_PER_MINUTE"]:
            return jsonify(
                {
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Try again in a few seconds.",
                }
            ), 429
        timestamps.append(now)
        return None

    def require_operator(view: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator that authenticates a short-lived operator JWT."""

        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            limited = enforce_rate_limit()
            if limited:
                return limited

            header = request.headers.get("Authorization", "")
            scheme, _, token = header.partition(" ")
            if scheme.lower() != "bearer" or not token:
                return _error("Send a bearer token in the Authorization header.", 401, "unauthorized")

            try:
                g.operator = decode_access_token(token, app.config["JWT_SECRET"])
            except TokenError as exc:
                audit_log.record(
                    actor="anonymous",
                    event_type="authentication_rejected",
                    outcome="rejected",
                    details={"message": str(exc)},
                )
                return _error(str(exc), 401, "unauthorized")
            return view(*args, **kwargs)

        return wrapped

    @app.get("/")
    def index() -> str:
        """Serve the single-page Dashboard used for the interactive demo."""

        return render_template("index.html")

    @app.get("/health")
    def health() -> Response:
        """Expose model and service status without requiring operator login."""

        return jsonify(
            {
                "service": "secure-real-time-traffic-demo",
                "status": "ok",
                "classifier": "SVM with RBF kernel",
                "congestion_threshold": CONGESTION_THRESHOLD,
                "model_metrics": classifier.metrics,
            }
        )

    @app.post("/auth/login")
    def login() -> Response | tuple[Response, int]:
        """Authenticate the demo operator and issue a short-lived JWT."""

        limited = enforce_rate_limit()
        if limited:
            return limited

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error("Request body must be a JSON object.", 400, "invalid_json")

        username = body.get("username")
        password = body.get("password")
        expected_username = app.config["DEMO_USERNAME"]
        expected_password = app.config["DEMO_PASSWORD"]
        credentials_match = (
            isinstance(username, str)
            and isinstance(password, str)
            and secrets.compare_digest(username, expected_username)
            and secrets.compare_digest(password, expected_password)
        )
        if not credentials_match:
            audit_log.record(
                actor=str(username) if isinstance(username, str) else "anonymous",
                event_type="login",
                outcome="rejected",
                details={"reason": "invalid credentials"},
            )
            return _error("Invalid username or password.", 401, "unauthorized")

        token = create_access_token(
            username,
            app.config["JWT_SECRET"],
            app.config["TOKEN_LIFETIME_MINUTES"],
        )
        audit_log.record(
            actor=username,
            event_type="login",
            outcome="accepted",
            details={"token_lifetime_minutes": app.config["TOKEN_LIFETIME_MINUTES"]},
        )
        return jsonify({"access_token": token, "token_type": "Bearer"})

    @app.get("/api/v1/demo/scenarios")
    @require_operator
    def demo_scenarios() -> Response:
        """Return safe, congested, tampered, and anomalous sensor examples."""

        scenarios = make_demo_scenarios(app.config["HMAC_SECRET"])
        return jsonify({"scenarios": scenarios})

    @app.post("/api/v1/traffic/evaluate")
    @require_operator
    def evaluate() -> Response | tuple[Response, int]:
        """Verify, validate, classify, and audit one signed sensor reading."""

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error("Request body must be a JSON object.", 400, "invalid_json")

        actor = getattr(g, "operator", "unknown-operator")
        road_id = body.get("road_id") if isinstance(body.get("road_id"), str) else None

        if not verify_payload_signature(body, app.config["HMAC_SECRET"]):
            audit_log.record(
                actor=actor,
                event_type="sensor_signature",
                road_id=road_id,
                outcome="rejected",
                details={"reason": "HMAC signature mismatch"},
            )
            return _error(
                "The sensor payload signature is invalid; the reading was not evaluated.",
                403,
                "invalid_signature",
            )

        try:
            validate_reading(body)
        except ReadingValidationError as exc:
            audit_log.record(
                actor=actor,
                event_type="sensor_validation",
                road_id=road_id,
                outcome="rejected",
                details={"reason": str(exc)},
            )
            return _error(str(exc), 422, "invalid_reading")

        result = classifier.evaluate(body)
        response = {
            "road_id": body["road_id"],
            "device_id": body["device_id"],
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            **result,
        }
        audit_log.record(
            actor=actor,
            event_type="traffic_evaluation",
            road_id=body["road_id"],
            outcome="accepted",
            details=response,
        )
        return jsonify(response)

    @app.post("/api/v1/traffic/batch")
    @require_operator
    def evaluate_batch() -> Response | tuple[Response, int]:
        """Evaluate a small batch by reusing the single-reading trust boundary."""

        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("readings"), list):
            return _error("Request body must contain a readings array.", 400, "invalid_json")
        if len(body["readings"]) > 50:
            return _error("A demo batch may contain at most 50 readings.", 413, "batch_too_large")

        # Reuse the same validation path instead of creating a second, subtly
        # different security implementation for batch requests.
        actor = getattr(g, "operator", "unknown-operator")
        results = []
        for reading in body["readings"]:
            if not isinstance(reading, dict):
                results.append({"status": "rejected", "error": "invalid_reading"})
                audit_log.record(
                    actor=actor,
                    event_type="batch_evaluation",
                    outcome="rejected",
                    details={"reason": "reading is not a JSON object"},
                )
                continue
            if not verify_payload_signature(reading, app.config["HMAC_SECRET"]):
                road_id = reading.get("road_id")
                results.append({"road_id": road_id, "status": "rejected", "error": "invalid_signature"})
                audit_log.record(
                    actor=actor,
                    event_type="batch_evaluation",
                    road_id=road_id if isinstance(road_id, str) else None,
                    outcome="rejected",
                    details={"reason": "HMAC signature mismatch"},
                )
                continue
            try:
                validate_reading(reading)
            except ReadingValidationError as exc:
                road_id = reading.get("road_id")
                results.append({"road_id": road_id, "status": "rejected", "error": str(exc)})
                audit_log.record(
                    actor=actor,
                    event_type="batch_evaluation",
                    road_id=road_id if isinstance(road_id, str) else None,
                    outcome="rejected",
                    details={"reason": str(exc)},
                )
                continue
            result = {"road_id": reading["road_id"], **classifier.evaluate(reading)}
            results.append(result)
            audit_log.record(
                actor=actor,
                event_type="batch_evaluation",
                road_id=reading["road_id"],
                outcome="accepted",
                details=result,
            )
        return jsonify({"count": len(results), "results": results})

    @app.get("/api/v1/traffic/history")
    @require_operator
    def history() -> Response:
        """Return recent audit events for the Dashboard timeline."""

        try:
            limit = int(request.args.get("limit", "20"))
        except ValueError:
            limit = 20
        return jsonify({"events": audit_log.recent(limit)})

    return app


app = create_app()


if __name__ == "__main__":
    port = _env_int("PORT", 5000)
    app.run(host="127.0.0.1", port=port, debug=False)
