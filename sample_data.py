"""Small deterministic data set used by the local traffic-congestion demo.

The presentation describes thirty records, split into twenty safe and ten
congested observations.  The values below intentionally remain synthetic: the
purpose of this project is to make the end-to-end system observable and easy to
run, not to claim that it is ready for real traffic-control decisions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from security import sign_payload


def build_training_rows() -> list[dict[str, Any]]:
    """Build 30 repeatable training rows with a clear congestion boundary.

    ``estimated_drho`` is deliberately constructed as ``rho_current`` minus
    ``rho_previous``.  This gives the API a simple data-quality invariant that
    can detect a signed but internally inconsistent reading.
    """

    rows: list[dict[str, Any]] = []

    # Twenty safe records stay below the presentation's d-rho threshold of 70.
    for index in range(20):
        previous = 5.0 + (index % 5) * 2.0
        estimated_drho = 45.0 + (index % 6) * 4.0
        rows.append(
            {
                "road_id": f"SAFE-{index + 1:02d}",
                "distance_km": 0.4 + (index % 4) * 0.1,
                "rho_current": previous + estimated_drho,
                "rho_previous": previous,
                "alpha": 0.20 + (index % 4) * 0.10,
                "estimated_drho": estimated_drho,
                "status": "safe",
            }
        )

    # Ten congested records are above the same threshold.
    for index in range(10):
        previous = 5.0 + (index % 4) * 3.0
        estimated_drho = 75.0 + (index % 5) * 15.0
        rows.append(
            {
                "road_id": f"CONGESTED-{index + 1:02d}",
                "distance_km": 0.5 + (index % 3) * 0.15,
                "rho_current": previous + estimated_drho,
                "rho_previous": previous,
                "alpha": 0.80 + (index % 3) * 0.20,
                "estimated_drho": estimated_drho,
                "status": "congested",
            }
        )

    return rows


TRAINING_ROWS = build_training_rows()


def _timestamp() -> str:
    """Return an ISO-8601 UTC timestamp suitable for a demo payload."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _payload(
    road_id: str,
    distance_km: float,
    rho_current: float,
    rho_previous: float,
    alpha: float,
    estimated_drho: float,
) -> dict[str, Any]:
    """Create a sensor payload without its signature field."""

    return {
        "road_id": road_id,
        "distance_km": distance_km,
        "rho_current": rho_current,
        "rho_previous": rho_previous,
        "alpha": alpha,
        "estimated_drho": estimated_drho,
        "device_id": "sensor-demo-01",
        "timestamp": _timestamp(),
    }


def make_demo_scenarios(hmac_secret: str) -> dict[str, dict[str, Any]]:
    """Return signed and intentionally unsafe payloads for the Dashboard.

    The browser receives these payloads just as a real sensor client would.  It
    never receives the HMAC secret.  The tampered case keeps the original
    signature after changing a field, while the anomaly case has a valid
    signature but violates the domain validation rules.
    """

    safe = _payload("FG", 0.5, 60.0, 5.0, 0.20, 55.0)
    congested = _payload("GB", 0.5, 80.0, 5.0, 0.80, 75.0)
    anomaly = _payload("BD_School", 0.5, 184.0, 10.0, 1.40, 250.0)

    safe["signature"] = sign_payload(safe, hmac_secret)
    congested["signature"] = sign_payload(congested, hmac_secret)
    anomaly["signature"] = sign_payload(anomaly, hmac_secret)

    tampered = dict(safe)
    tampered["road_id"] = "FG-TAMPERED"
    tampered["estimated_drho"] = 150.0

    return {
        "safe": {
            "label": "Safe reading",
            "description": "Valid FG reading below the d-rho threshold.",
            "payload": safe,
        },
        "congested": {
            "label": "Congested reading",
            "description": "Valid GB reading above the d-rho threshold.",
            "payload": congested,
        },
        "tampered": {
            "label": "Tampered payload",
            "description": "The road and d-rho values were changed after signing.",
            "payload": tampered,
        },
        "anomaly": {
            "label": "Signed anomaly",
            "description": "The signature is valid, but d-rho is outside the accepted range.",
            "payload": anomaly,
        },
    }

