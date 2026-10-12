"""Owned internal operator pilot; credentials are read from read-only mounts at startup."""

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rag_portfolio.medical_release_service import create_release_app


def create_app():
    config = json.loads(Path(os.environ["MEDICAL_PILOT_CONFIG"]).read_text(encoding="utf8"))
    if (
        config.get("scope_key") != "medical-business-v1"
        or config.get("clinical_approval") is not False
    ):
        raise ValueError("Internal nonclinical scope required")
    # Existing Settings loads .env in cwd; this mounted file never enters image or logs.
    os.chdir("/run/model-config")
    app = create_release_app(
        config["registry_path"], config["rag_token"], max_inflight=2, trace_limit=100
    )
    logger = logging.getLogger("medical_pilot.audit")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        "/var/log/pilot/requests.jsonl", maxBytes=1_000_000, backupCount=3, encoding="utf8"
    )
    logger.addHandler(handler)

    @app.middleware("http")
    async def audit(request, call_next):
        response = await call_next(request)
        logger.info(json.dumps({"path": request.url.path, "http": response.status_code}))
        return response

    return app
