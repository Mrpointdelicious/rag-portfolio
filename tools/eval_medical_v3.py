"""Freeze v3 reference evidence, run development, then open its new holdout once."""

import argparse
import asyncio
import json
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from rag_portfolio.adapters.embedding import EmbeddingAdapter
from rag_portfolio.config import Settings
from rag_portfolio.evaluation.medical_corpus import digest
from rag_portfolio.evaluation.medical_runner import embedding_revision, write_report
from rag_portfolio.evaluation.medical_v3 import EXPERIMENT, GoldV3, SuiteV3, freeze, select_variant
from rag_portfolio.evaluation.medical_v3_runner import RerankerV3, run
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.literature_backup import LiteratureError, write_json


async def evaluate(root, split):
    public, base = root / "eval" / EXPERIMENT, root / ".local" / EXPERIMENT
    suite = SuiteV3.model_validate_json((public / "cases.json").read_text(encoding="utf8"))
    gold = GoldV3.model_validate_json((public / "gold.json").read_text(encoding="utf8"))
    settings = Settings()
    adapter = EmbeddingAdapter(settings)
    reranker = RerankerV3(
        settings,
        base / "rerank_cache.json",
        inherit=root / ".local/medical_rehab_v2/rerank_cache.json",
    )
    client = AsyncQdrantClient(path=str(base / "qdrant"))
    try:
        return await run(
            root=root,
            suite=suite,
            gold=gold,
            settings=settings,
            adapter=adapter,
            client=client,
            reranker=reranker,
            revision=embedding_revision(settings),
            split=split,
        )
    finally:
        await adapter.close()
        await reranker.close()
        await client.close()


def write_context_packs(report, run_path, selected, root):
    lines = [
        "# 按开发集选择的上下文证据包",
        "",
        f"检索方式：{selected}。证据预算为4000个cl100k_base参考token，非模型账单token。",
        "",
        "这里展示原文证据和输入缺失状态，未生成临床回答或患者数值。",
        "",
    ]
    for row in report["cases"]:
        pack = row["variants"][selected]["context_pack"]
        lines.extend(
            [
                f"## {row['id']} {row['query']}",
                "",
                f"输入处理：{row['input_action']}；参考token：{pack['reference_tokens_used']}。",
                "",
            ]
        )
        if not pack["cards"]:
            lines.extend(["缺少本题所需的患者或实时业务输入；文献不能提供该实际数值。", ""])
        for card in pack["cards"]:
            source = (root / "doc" / card["source_path"]).resolve()
            if not source.is_relative_to((root / "doc/selected").resolve()):
                raise LiteratureError("Context PDF outside selected backup folder")
            lines.extend(
                [
                    f"[{card['title']}](<{source.as_posix()}>)；PDF第{card['pdf_page_start']}页；字符{card['start_char']}–{card['end_char']}。",
                    "",
                    card["text"],
                    "",
                ]
            )
    (run_path / "context_packs.md").write_text("\n".join(lines) + "\n", encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--open-sealed-test", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = root / ".local" / EXPERIMENT
    try:
        if args.freeze:
            gold = freeze(root)
            print(f"Frozen {len(gold.cases)} cases; holdout seal {gold.test_seal}")
            return 0
        if args.split == "test" and not args.open_sealed_test:
            raise LiteratureError("Open new holdout only after freezing development selection")
        report = asyncio.run(evaluate(root, args.split), loop_factory=loop_factory)
        if args.split == "dev":
            selected = select_variant(report["summary"])
            selection = {
                "variant": selected,
                "dev_report_fingerprint": digest(report),
                "config_fingerprint": digest(report["config"]),
                "test_seal": report["test_seal"],
            }
            write_json(base / "selection.json", selection)
        else:
            selected = json.loads((base / "selection.json").read_text(encoding="utf8"))["variant"]
        run_path = write_report(report, base / "results" / args.split, backup_root=root / "doc")
        write_context_packs(report, run_path, selected, root)
        print(f"Complete medical v3 {args.split}: {run_path}; selected={selected}")
        for name, summary in report["summary"].items():
            print(name, {k: v for k, v in summary.items() if k != "per_category"})
        print("input routing", report["input_action_summary"])
        return 0
    except LiteratureError as exc:
        print(f"Medical v3 failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
