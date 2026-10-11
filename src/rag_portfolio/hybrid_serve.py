"""Opt-in standalone launcher, loads provider secrets without logging configuration."""

import argparse

import uvicorn

from rag_portfolio.api.hybrid import create_app
from rag_portfolio.config import Settings
from rag_portfolio.services.hybrid_retrieval import HybridSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18102)
    args = parser.parse_args()
    config = HybridSettings()
    # Existing .env is read only by the provider config; no DB/vector connection is made.
    provider = Settings()
    uvicorn.run(create_app(config, provider), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
