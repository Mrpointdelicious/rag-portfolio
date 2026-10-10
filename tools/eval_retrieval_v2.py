"""Replay frozen Dense v1 and compare eval-only role metadata and BM25/RRF with v2 Gold."""

import argparse
import asyncio
import sys
from pathlib import Path

from rag_portfolio.config import Settings
from rag_portfolio.db.session import Database
from rag_portfolio.evaluation.corpus import DEFAULT_CORPUS_PATH, EvaluationError, read_snapshot
from rag_portfolio.evaluation.v2_runner import run_v2, validate_output_dir, write_v2_reports
from rag_portfolio.evaluation.v2_schema import (
    DEFAULT_DENSE_SOURCE,
    DEFAULT_V2_OUTPUT,
    DEFAULT_V2_SUITE,
    read_dense_source,
    read_v2_suite,
)
from rag_portfolio.event_loop import loop_factory


async def evaluate(args):
    suite = read_v2_suite(args.suite)
    snapshot = read_snapshot(args.corpus)
    source = read_dense_source(args.dense_source, suite)
    database = Database(Settings())
    try:
        async with database.sessions() as session:
            return await run_v2(session, suite, snapshot, source)
    finally:
        await database.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_V2_SUITE)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--dense-source", type=Path, default=DEFAULT_DENSE_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_V2_OUTPUT)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        validate_output_dir(args.output_dir, [args.suite, args.corpus, args.dense_source])
        reports = asyncio.run(evaluate(args), loop_factory=loop_factory)
        archive = write_v2_reports(reports, args.output_dir)
        for name, report in reports.items():
            if "overall" in report:
                print(f"{name}: {report['status']} {report['overall']}")
        print(f"Immutable run: {archive}")
        print(f"Comparison: {args.output_dir / 'comparison.md'}")
    except EvaluationError as exc:
        print(f"FAIL: {exc}; no complete evaluation published for this attempt")
        return 1
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}; check local dependencies; no completed run reported")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
