"""Start only on loopback; caller supplies a distinct ephemeral token in memory/env."""

import argparse
import json
import os
from pathlib import Path

import uvicorn

from rag_portfolio.public_medical_service import create_medical_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    corpus = json.loads(
        Path("artifacts/public-medical-corpus-v1/public-prose.json").read_text(encoding="utf8")
    )
    index = json.loads(
        Path(".local/public_medical_corpus_v1/indexes/medical-public-v1-dense-1.json").read_text(
            encoding="utf8"
        )
    )
    uvicorn.run(
        create_medical_app(corpus, index, os.environ["PUBLIC_MEDICAL_TEST_TOKEN"]),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )
