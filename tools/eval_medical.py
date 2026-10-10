"""Run only medical original-PDF retrieval, using its own corpus, Gold, cache and index."""

import argparse
import asyncio
import json
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from rag_portfolio.adapters.embedding import EmbeddingAdapter
from rag_portfolio.config import Settings
from rag_portfolio.evaluation.experiments import medical_path
from rag_portfolio.evaluation.medical_runner import (
    embedding_revision,
    run_medical,
    validate_test_prerequisites,
    write_report,
)
from rag_portfolio.evaluation.medical_schema import MedicalGold, read_suite
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.literature_backup import LiteratureError


async def evaluate(args, root: Path) -> dict:
    suite_path = medical_path(root, args.suite, public=True)
    gold_path = medical_path(root, args.gold, public=True)
    corpus_path = medical_path(root, args.corpus)
    suite = read_suite(suite_path)
    gold = MedicalGold.model_validate_json(gold_path.read_text(encoding="utf-8"))
    if args.split == "test" and not args.open_sealed_test:
        raise LiteratureError(
            "Use --open-sealed-test only after freezing the development configuration"
        )
    settings = Settings()
    if args.split == "test":
        dev_path = root / ".local/medical_rehab_v1/results/dev/latest.json"
        if not dev_path.exists():
            raise LiteratureError("A complete development run is required before opening test")
        dev = json.loads(dev_path.read_text(encoding="utf-8"))
        validate_test_prerequisites(dev, gold, embedding_revision(settings))
    adapter = EmbeddingAdapter(settings)
    client = AsyncQdrantClient(path=str(root / ".local/medical_rehab_v1/qdrant"))
    try:
        report = await run_medical(
            suite=suite,
            gold=gold,
            corpus_path=corpus_path,
            backup_root=root / "doc",
            adapter=adapter,
            client=client,
            revision=embedding_revision(settings),
            dimension=settings.embedding_dimension,
            cache_root=root / ".local/medical_rehab_v1/embeddings",
            split=args.split,
        )
        report["embedding_model"] = settings.embedding_model
        report["embedding_dimension"] = settings.embedding_dimension
        report["embedding_provider"] = settings.embedding_provider
        return report
    finally:
        await adapter.close()
        await client.close()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=root / "eval/medical_rehab_v1/cases.json")
    parser.add_argument("--gold", type=Path, default=root / "eval/medical_rehab_v1/gold.json")
    parser.add_argument(
        "--corpus", type=Path, default=root / ".local/medical_rehab_v1/corpus/manifest.json"
    )
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--open-sealed-test", action="store_true")
    args = parser.parse_args()
    try:
        report = asyncio.run(evaluate(args, root), loop_factory=loop_factory)
        run = write_report(
            report,
            root / ".local/medical_rehab_v1/results" / args.split,
            backup_root=root / "doc",
        )
        print(f"Complete medical {args.split} run: {run}")
        for name, metrics in report["summary"].items():
            print(name, metrics)
    except Exception as exc:
        # Provider exceptions deliberately redact credentials and HTTP bodies.
        print(f"Medical experiment failed ({type(exc).__name__}): {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
