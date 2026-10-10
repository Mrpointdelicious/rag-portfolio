"""Evaluate the frozen five-document Dense corpus; reports stay local, no audience filter."""

import argparse
import asyncio
import sys
from pathlib import Path

from rag_portfolio.adapters.embedding import EmbeddingAdapterError
from rag_portfolio.adapters.vector import VectorAdapterError
from rag_portfolio.config import Settings
from rag_portfolio.evaluation.corpus import DEFAULT_CORPUS_PATH, EvaluationError, read_snapshot
from rag_portfolio.evaluation.retrieval import (
    COLLECTION,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SUITE_PATH,
    read_suite,
    render_report,
    run_evaluation,
    write_report,
)
from rag_portfolio.event_loop import loop_factory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        type=Path,
        default=DEFAULT_SUITE_PATH,
        help="Fixed cases JSON; default: %(default)s",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS_PATH,
        help="Approved corpus snapshot; default: %(default)s",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Local latest.json/latest.md and run history; default: %(default)s",
    )
    parser.add_argument(
        "--recreate", action="store_true", help=f"Delete and recreate only {COLLECTION}"
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        suite, snapshot = read_suite(args.suite), read_snapshot(args.corpus)
        report = asyncio.run(
            run_evaluation(Settings(), suite, snapshot, recreate=args.recreate),
            loop_factory=loop_factory,
        )
        write_report(report, args.output_dir)
        print(render_report(report))
        print(f"Reports: {args.output_dir / 'latest.json'} / {args.output_dir / 'latest.md'}")
    except (EvaluationError, EmbeddingAdapterError, VectorAdapterError) as exc:
        print(f"FAIL: {exc}; no complete report published for this attempt")
        return 1
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}; no complete report published; check local dependencies")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
