"""Opt-in demo launcher. Ignores .env and never connects to a production database."""

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from rag_portfolio.config import Settings
from rag_portfolio.main import create_app


def app():
    root = Path(__file__).resolve().parents[1]
    return create_app(
        Settings(
            _env_file=None,
            environment="development",
            demo_retrieval_enabled=True,
            demo_token=os.environ.get("RAG_DEMO_TOKEN", ""),
            demo_corpus_path=os.environ.get(
                "RAG_DEMO_CORPUS_PATH", str(root / "fixtures/integration_demo/corpus.json")
            ),
            demo_corpus_sha256=os.environ.get("RAG_DEMO_CORPUS_SHA256", ""),
        )
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18092)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app(), host=args.host, port=args.port, log_level="info")
