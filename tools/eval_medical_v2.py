"""Freeze medical v2 Gold or compare isolated retrieval variants on real providers."""

import argparse
import asyncio
import json
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from rag_portfolio.adapters.embedding import EmbeddingAdapter
from rag_portfolio.config import Settings
from rag_portfolio.evaluation.medical_corpus import digest
from rag_portfolio.evaluation.medical_runner import embedding_revision, write_report
from rag_portfolio.evaluation.medical_v2 import (
    EXPERIMENT,
    GoldV2,
    Reranker,
    SuiteV2,
    freeze,
    run_v2,
    select_variant,
)
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.literature_backup import LiteratureError, write_json


async def evaluate(root: Path, split: str) -> dict:
    public = root / "eval" / EXPERIMENT
    private = root / ".local" / EXPERIMENT
    suite = SuiteV2.model_validate_json((public / "cases.json").read_text(encoding="utf8"))
    gold = GoldV2.model_validate_json((public / "gold.json").read_text(encoding="utf8"))
    aliases = json.loads((public / "source_aliases.json").read_text(encoding="utf8"))
    settings = Settings()
    adapter = EmbeddingAdapter(settings)
    reranker = Reranker(settings, private / "rerank_cache.json")
    client = AsyncQdrantClient(path=str(private / "qdrant"))
    try:
        return await run_v2(
            root=root,
            suite=suite,
            gold=gold,
            settings=settings,
            adapter=adapter,
            client=client,
            reranker=reranker,
            split=split,
            revision=embedding_revision(settings),
            aliases=aliases,
        )
    finally:
        await adapter.close()
        await reranker.close()
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--open-sealed-test", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    private = root / ".local" / EXPERIMENT
    try:
        if args.freeze:
            gold = freeze(root)
            print(f"Frozen {len(gold.cases)} medical v2 cases; test seal: {gold.test_seal}")
            return 0
        if args.split == "test" and not args.open_sealed_test:
            raise LiteratureError(
                "Opening holdout requires --open-sealed-test after development selection"
            )
        report = asyncio.run(evaluate(root, args.split), loop_factory=loop_factory)
        if args.split == "dev":
            selection = {
                "dev_report_fingerprint": digest(report),
                "variant": select_variant(report["summary"]),
                "config_fingerprint": digest(report["config"]),
                "test_seal": report["test_seal"],
            }
            destination = private / "selection.json"
            if (
                destination.exists()
                and json.loads(destination.read_text(encoding="utf8")) != selection
            ):
                raise LiteratureError("Selection is frozen; use a new revision for further tuning")
            write_json(destination, selection)
        run = write_report(report, private / "results" / args.split, backup_root=root / "doc")
        print(f"Complete {args.split}: {run}")
        for name, metrics in report["summary"].items():
            print(name, {k: v for k, v in metrics.items() if k != "per_category"})
        return 0
    except LiteratureError as exc:
        print(f"Medical v2 failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
