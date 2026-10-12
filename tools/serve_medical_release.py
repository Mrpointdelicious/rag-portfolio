"""Owned loopback release service with optional explicit fault injection for EVAL."""

import argparse
import os
from pathlib import Path

import uvicorn

from rag_portfolio.medical_release_service import create_release_app
from rag_portfolio.public_medical import write_json

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--registry", type=Path, required=True)
    p.add_argument("--fault-file", type=Path)
    p.add_argument("--trace", type=Path, required=True)
    args = p.parse_args()
    app = create_release_app(
        args.registry, os.environ["MEDICAL_RELEASE_TEST_TOKEN"], fault_file=args.fault_file
    )

    @app.middleware("http")
    async def save_trace(request, call_next):
        try:
            return await call_next(request)
        finally:
            write_json(args.trace, app.state.calls)

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
