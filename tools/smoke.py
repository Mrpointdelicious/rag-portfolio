"""Validate the local API and worker without printing credentials or calling production services."""

import argparse
import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from rag_portfolio.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5052")
    args = parser.parse_args()
    if urlparse(args.url).hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Smoke checks only target a local development service.")
    settings = Settings()
    with httpx.Client(base_url=args.url, timeout=10) as client:
        ready = client.get("/health/ready")
        ready.raise_for_status()
        assert ready.json()["status"] == "ready"
        assert client.get("/v1/knowledge-bases").status_code == 401
        admin = {"Authorization": f"Bearer {settings.admin_token.get_secret_value()}"}
        reader = {"Authorization": f"Bearer {settings.service_token.get_secret_value()}"}
        listing = client.get("/v1/knowledge-bases", headers=admin)
        listing.raise_for_status()
        bases = listing.json()
        existing = next(
            (row for row in bases if row["code"] == "rehab_kb" and row["scope_key"] == "shared"),
            None,
        )
        if existing is None:
            response = client.post("/v1/knowledge-bases", headers=admin, json={"code": "rehab_kb"})
            response.raise_for_status()
            existing = response.json()
            assert not existing["enabled"]
        probe_headers = {**admin, "Idempotency-Key": str(uuid4())}
        first = client.post("/v1/jobs/probes", headers=probe_headers)
        first.raise_for_status()
        second = client.post("/v1/jobs/probes", headers=probe_headers)
        second.raise_for_status()
        job_id = first.json()["id"]
        assert second.json()["id"] == job_id
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = client.get(f"/v1/jobs/{job_id}", headers=admin)
            response.raise_for_status()
            job = response.json()
            if job["status"] == "succeeded":
                assert job["result_json"] == {"probe": "ok"}
                break
            if job["status"] == "failed":
                raise SystemExit(f"Probe failed: {job['last_error_code']}")
            time.sleep(0.5)
        else:
            raise SystemExit("Probe timed out; confirm that the worker is running.")
        response = client.post(
            "/v1/retrieve",
            headers=reader,
            json={"kb_ids": [existing["id"]], "queries": [{"q": "framework probe"}]},
        )
        assert response.status_code == 501
        print(
            "PASS: readiness, authentication, KB registration, "
            "idempotency, worker, retrieval boundary"
        )


if __name__ == "__main__":
    main()
