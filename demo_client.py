"""Command-line sensor simulator for the local traffic-congestion demo.

Start the server first, then run ``python demo_client.py`` from this project
directory.  The script deliberately uses Python's standard-library HTTP client
so the demonstration does not need an additional ``requests`` dependency.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


def request_json(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Send one JSON request and return its JSON response."""

    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def main() -> None:
    """Run the four scenarios that make the security boundary visible."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--username", default="operator")
    parser.add_argument("--password", default="demo-password")
    args = parser.parse_args()

    login = request_json(
        args.base_url,
        "POST",
        "/auth/login",
        {"username": args.username, "password": args.password},
    )
    token = login.get("access_token")
    if not token:
        raise SystemExit(f"Login failed: {json.dumps(login, indent=2)}")

    scenarios = request_json(args.base_url, "GET", "/api/v1/demo/scenarios", token=token)
    for name, scenario in scenarios.get("scenarios", {}).items():
        result = request_json(
            args.base_url,
            "POST",
            "/api/v1/traffic/evaluate",
            scenario["payload"],
            token=token,
        )
        print(f"\n{name.upper()}: {scenario['description']}")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

