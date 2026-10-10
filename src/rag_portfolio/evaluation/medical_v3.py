"""Source-span evaluation and query-only planning for the third medical experiment."""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from rag_portfolio.evaluation.medical_corpus import digest, load_corpus, text_digest
from rag_portfolio.evaluation.medical_schema import (
    MedicalCase,
    MedicalGold,
    MedicalSuite,
    test_seal,
    validate_gold,
)
from rag_portfolio.evaluation.medical_v2 import anchor_span, resolve_sources
from rag_portfolio.literature_backup import LiteratureError, write_json

EXPERIMENT = "medical_rehab_v3"
DOMAIN = "medical_knowledge"
VARIANTS = (
    "hybrid",
    "reranked_hybrid",
    "v2_source_routed_rerank",
    "preserved_query",
    "decomposed",
    "coverage",
    "context_expanded",
)
ACTION = Literal[
    "retrieve_literature",
    "literature_with_patient_limit",
    "request_patient_data",
    "needs_live_data",
    "check_corpus_inventory",
    "unsupported_secret",
    "provenance_only",
]
FACETS = (
    (
        "population",
        r"研究对象|纳入.*人群|人群|年龄|对象|使用者|职业|population|participants|eligib",
        "研究对象 年龄 疾病 纳入人群 使用者 target population participants "
        "age inclusion criteria users",
    ),
    (
        "intervention",
        r"干预|对照|intervention|control|剂量",
        "干预 对照训练 剂量匹配 intervention control treatment "
        "conventional gait training dose matched",
    ),
    (
        "outcome",
        r"结局|量表|主要指标|outcome|BBS|TUG",
        "主要次要结局 平衡量表 primary secondary outcome mobility capacity balance Berg TUG",
    ),
    (
        "aim",
        r"目的|主题|范围|回答的问题|scope|aim|objective",
        "制定目的 主题 适用范围 objective aim scope purpose of this study guideline",
    ),
    (
        "grading",
        r"分级|证据方法|推荐强度|GRADE|grading",
        "证据分级 推荐强度 evidence grading recommendation strength GRADE Oxford OCEBM",
    ),
    (
        "quality",
        r"质量|偏倚|工具|评估哪些方面|quality|bias|AGREE|PEDro",
        "方法学质量 评估范围 偏倚评价 工具 quality risk of bias assessment AGREE II PEDro",
    ),
    (
        "limitations",
        r"局限|限制|异质性|样本量|limitations|heterogene",
        "研究局限 样本量 异质性 limitations sample size heterogeneity methodological flaws",
    ),
    (
        "registration",
        r"注册|registration|PROSPERO",
        "注册平台 注册编号 registration registry PROSPERO PREPARE",
    ),
    (
        "search",
        r"检索|数据库|截止|语言|English|Italian|database|search",
        "检索数据库 截止日期 语言 纳入条件 databases search date "
        "language inclusion English Italian",
    ),
    (
        "methods",
        r"德尔菲|问卷|投票|比例|合并|条目|意见|Delphi",
        "制订方法 德尔菲 投票 推荐意见 最终条目 Delphi consensus vote methods final items",
    ),
    (
        "stage",
        r"病程|急性期|恢复期|后遗症|阶段|stage",
        "病程 急性期 恢复期 后遗症期 阶段 acute recovery chronic stage time since stroke",
    ),
    (
        "definition",
        r"定义|术语|质心|重心|静态|动态|稳定极限|define",
        "术语 定义 静态动态平衡 质心重心 definitions static dynamic balance center mass gravity",
    ),
    (
        "version",
        r"年份|哪一年|版本|版次|更新|标准|edition|published",
        "版本年 期刊发表年 更新 引用标准 edition publication year update normative references",
    ),
)
CONFIG = {
    "candidate_depth": 20,
    "candidate_pool_cap": 80,
    "final_depth": 5,
    "rrf_k": 60,
    "max_facets": 3,
    "max_tasks": 6,
    "planner": "query-only-source-title-bilingual-facets-v1",
    "facet_rules_fingerprint": digest(FACETS),
    "selection_relevance_weight": 0.55,
    "selection_task_gain_weight": 0.45,
    "selection_prefix": 8,
    "expansion_chars": 240,
    "context_budget": 4000,
    "context_tokenizer": "cl100k_base",
    "context_max_seeds": 8,
    "tokenizer_is_provider_billing_tokenizer": False,
    "metrics": "reviewed-source-span-union-v1",
    "selection": "budget_complete,top5_complete,budget_coverage,mrr,variant-order",
}


class CaseV3(MedicalCase):
    expected_action: ACTION


class SuiteV3(MedicalSuite):
    experiment_id: Literal["medical_rehab_v3"]
    cases: tuple[CaseV3, ...] = Field(min_length=1)


class GoldV3(MedicalGold):
    experiment_id: Literal["medical_rehab_v3"]
    review_policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def freeze(root: Path) -> GoldV3:
    public = root / "eval" / EXPERIMENT
    suite = SuiteV3.model_validate_json((public / "cases.json").read_text(encoding="utf8"))
    reviews = json.loads((public / "source_reviews.json").read_text(encoding="utf8"))
    policy = json.loads((public / "review_policy.json").read_text(encoding="utf8"))
    for item in (reviews, policy):
        if (
            item.get("domain") != DOMAIN
            or item.get("experiment_id") != EXPERIMENT
            or item.get("clinical_approval") is not False
        ):
            raise LiteratureError("Explicit independent medical v3 namespace required")
    manifest, pages, chunks = load_corpus(
        root / ".local/medical_rehab_v1/corpus/manifest.json", root / "doc"
    )
    lookup = {(p["document_id"], p["pdf_page"]): p for p in pages}
    cases = []
    for review in reviews["cases"]:
        groups = [
            {
                "id": g["id"],
                "support": g["support"],
                "alternatives": [
                    anchor_span(lookup[(a["document_id"], a["pdf_page"])], a["anchor"])
                    for a in g["alternatives"]
                ],
            }
            for g in review["required_groups"]
        ]
        cases.append(
            {k: v for k, v in review.items() if k != "required_groups"}
            | {"required_groups": groups}
        )
    gold = GoldV3.model_validate(
        {
            "schema_version": 1,
            "domain": DOMAIN,
            "experiment_id": EXPERIMENT,
            "corpus_fingerprint": manifest["corpus_fingerprint"],
            "suite_fingerprint": digest(suite.model_dump(mode="json")),
            "status": "frozen_technical_review",
            "clinical_approval": False,
            "cases": cases,
            "review_policy_fingerprint": digest(policy),
            "test_seal": test_seal(suite, cases, manifest["corpus_fingerprint"]),
        }
    )
    validate_gold(gold, suite, manifest, pages, chunks)
    destination = public / "gold.json"
    if destination.exists() and json.loads(
        destination.read_text(encoding="utf8")
    ) != gold.model_dump(mode="json"):
        raise LiteratureError("V3 Gold is already sealed; create a new revision")
    write_json(destination, gold.model_dump(mode="json"))
    seal_path = root / ".local" / EXPERIMENT / "freeze_record.json"
    seal = {
        "test_seal": gold.test_seal,
        "suite_fingerprint": gold.suite_fingerprint,
        "review_policy_fingerprint": gold.review_policy_fingerprint,
        "corpus_fingerprint": gold.corpus_fingerprint,
    }
    if seal_path.exists() and json.loads(seal_path.read_text(encoding="utf8")) != seal:
        raise LiteratureError("Medical v3 freeze record differs")
    write_json(seal_path, seal)
    return gold


@dataclass(frozen=True)
class Task:
    id: str
    document_id: str | None
    facet: str
    query: str


def plan_query(query: str, aliases: dict, documents: dict) -> tuple[list[str], list[Task]]:
    sources, _ = resolve_sources(query, aliases)
    # Match the original question. Never delete a disease or intervention with its title.
    facets = [(name, terms) for name, pattern, terms in FACETS if re.search(pattern, query, re.I)][
        :3
    ]
    if not facets:
        facets = [("question", query)]
    targets = sources or [None]
    tasks = []
    for facet, terms in facets:
        for doc in targets:
            title = documents[doc]["title"] if doc is not None else query
            tasks.append(Task(f"{doc or 'all'}:{facet}", doc, facet, title + "\n" + terms))
    return sources, tasks[:6]


def input_action(query: str, aliases: dict) -> str:
    sources, _ = resolve_sources(query, aliases)
    patient_request = bool(
        re.search(
            r"实际测|实际走|测了|用了多少秒|测量记录|病历|住院影像|训练.*(?:几次|次数)|(?:我|本人).*康复训练|个体化.*处方",
            query,
        )
    )
    if patient_request and not (
        sources and re.search(r"范围|纳入|能提供哪些信息|能否直接确定", query)
    ):
        return "request_patient_data"
    if re.search(
        r"(?:医院|门诊|挂号|预约).*(?:下周|今天|时段|名额)|接口延迟|真实接口|生产并发|实时.*(?:延迟|并发)",
        query,
    ):
        return "needs_live_data"
    if re.search(r"(?:所有|全部).*(?:指南|文献)|收录.*更新版", query):
        return "check_corpus_inventory"
    if re.search(r"维修密码|设备.*密码|密钥", query):
        return "unsupported_secret"
    if re.search(r"文件.*内容相同|同一共识.*两份文件|两个独立证据", query):
        return "provenance_only"
    if sources and re.search(r"未提供病情|个体训练|(?:个人|个体)临床适应", query):
        return "literature_with_patient_limit"
    return "retrieve_literature"


def round_robin_pool(rankings: list[list], cap: int = 80) -> list:
    seen, result = set(), []
    for rank in range(max((len(r) for r in rankings), default=0)):
        for ranking in rankings:
            if rank < len(ranking) and ranking[rank].chunk_id not in seen:
                result.append(ranking[rank])
                seen.add(ranking[rank].chunk_id)
                if len(result) == cap:
                    return result
    return result


def coverage_order(ranked: list, task_rankings: dict[str, list]) -> tuple[list, list[dict]]:
    """Facility-coverage gain uses actual subquery ranks, never reference evidence groups."""
    affinity = {}
    for task, hits in task_rankings.items():
        maximum = max((h.score for h in hits), default=1.0)
        affinity[task] = {str(h.chunk_id): h.score / maximum for h in hits} if maximum > 0 else {}
    covered = dict.fromkeys(affinity, 0.0)
    remaining, chosen, audit = list(ranked), [], []
    for _ in range(min(CONFIG["selection_prefix"], len(ranked))):

        def objective(pair):
            index, hit = pair
            gain = sum(
                max(0.0, values.get(str(hit.chunk_id), 0.0) - covered[task])
                for task, values in affinity.items()
            ) / max(1, len(affinity))
            score = (
                CONFIG["selection_relevance_weight"] * hit.score
                + CONFIG["selection_task_gain_weight"] * gain
            )
            return score, -index

        index, hit = max(enumerate(remaining), key=objective)
        score, _ = objective((index, hit))
        chosen.append(hit)
        remaining.pop(index)
        for task, values in affinity.items():
            covered[task] = max(covered[task], values.get(str(hit.chunk_id), 0.0))
        audit.append(
            {
                "chunk_id": str(hit.chunk_id),
                "objective": score,
                "task_affinity_maxima": dict(covered),
            }
        )
    return chosen + remaining, audit


def span_covered(ref, cards: list[dict]) -> bool:
    cursor = ref.start_char
    intervals = sorted(
        (c["start_char"], c["end_char"])
        for c in cards
        if c["document_id"] == ref.document_id and c["pdf_page_start"] == ref.pdf_page
    )
    for start, end in intervals:
        if start <= cursor:
            cursor = max(cursor, end)
    return cursor >= ref.end_char


def group_hits(cards: list[dict], gold_case) -> list[bool]:
    return [
        any(span_covered(ref, cards) for ref in group.alternatives)
        for group in gold_case.required_groups
    ]


def metrics(cards: list[dict], gold_case) -> dict | None:
    if not gold_case.scored:
        return None
    hits = group_hits(cards[:5], gold_case)
    first = next(
        (i for i in range(1, min(5, len(cards)) + 1) if any(group_hits(cards[:i], gold_case))), None
    )
    return {
        "hit_at_1": float(bool(cards) and any(group_hits(cards[:1], gold_case))),
        "hit_at_5": float(first is not None),
        "mrr_at_5": 1 / first if first else 0.0,
        "required_evidence_coverage": sum(hits) / len(hits),
        "complete_evidence_at_5": float(all(hits)),
    }


def budget_metrics(cards: list[dict], gold_case) -> dict | None:
    if not gold_case.scored:
        return None
    hits = group_hits(cards, gold_case)
    return {
        "required_evidence_coverage": sum(hits) / len(hits),
        "complete_evidence": float(all(hits)),
    }


class ContextPacker:
    def __init__(self, pages: list[dict], chunks: dict):
        import tiktoken

        self.pages = {(p["document_id"], p["pdf_page"]): p for p in pages}
        self.chunks = chunks
        self.encoder = tiktoken.get_encoding(CONFIG["context_tokenizer"])

    @staticmethod
    def render(cards: list[dict]) -> str:
        return "\n\n".join(
            f"[{i}] {c['document_id']} {c['title']} PDF {c['pdf_page_start']} "
            f"chars {c['start_char']}:{c['end_char']}\n{c['text']}"
            for i, c in enumerate(cards, 1)
        )

    def tokens(self, cards: list[dict]) -> int:
        return len(self.encoder.encode(self.render(cards), disallowed_special=()))

    def card(self, key: str, *, expand: bool) -> dict:
        chunk = self.chunks[key]
        page = self.pages[(chunk["document_id"], chunk["pdf_page_start"])]
        if page["quality_flags"]:
            raise LiteratureError("Unresolved PDF page cannot enter a context pack")
        start, end = chunk["start_char"], chunk["end_char"]
        if expand:
            # Add only nearby same-page source text, preserving all negation and numbers.
            lower, upper = max(0, start - 240), min(len(page["text"]), end + 240)
            before = list(re.finditer(r"[。！？!?]\s*|\.(?=\s+[A-Z])", page["text"][lower:start]))
            after = re.search(r"[。！？!?]\s*|\.(?=\s+[A-Z])", page["text"][end:upper])
            start = lower + before[-1].end() if before else lower
            end = end + after.end() if after else upper
        return {
            "document_id": chunk["document_id"],
            "title": chunk["title"],
            "source_path": chunk["source_path"],
            "source_sha256": chunk["source_sha256"],
            "pdf_page_start": chunk["pdf_page_start"],
            "pdf_page_end": chunk["pdf_page_start"],
            "start_char": start,
            "end_char": end,
            "text": page["text"][start:end],
            "text_sha256": text_digest(page["text"][start:end]),
            "seed_ids": [key],
        }

    def _merge(self, cards: list[dict], incoming: dict) -> list[dict]:
        merged = dict(incoming)
        result, insert_at = [], None
        for card in cards:
            same_page = (card["document_id"], card["pdf_page_start"]) == (
                merged["document_id"],
                merged["pdf_page_start"],
            )
            if same_page and max(card["start_char"], merged["start_char"]) <= min(
                card["end_char"], merged["end_char"]
            ):
                insert_at = len(result) if insert_at is None else insert_at
                merged["start_char"] = min(card["start_char"], merged["start_char"])
                merged["end_char"] = max(card["end_char"], merged["end_char"])
                merged["seed_ids"] = list(dict.fromkeys(card["seed_ids"] + merged["seed_ids"]))
            else:
                result.append(card)
        page = self.pages[(merged["document_id"], merged["pdf_page_start"])]
        merged["text"] = page["text"][merged["start_char"] : merged["end_char"]]
        merged["text_sha256"] = text_digest(merged["text"])
        result.insert(len(result) if insert_at is None else insert_at, merged)
        return result

    def pack(self, candidates: list, *, expand: bool = False) -> dict:
        cards, accepted = [], []
        for hit in candidates:
            key = str(hit.chunk_id)
            tentative = self._merge(cards, self.card(key, expand=expand))
            if self.tokens(tentative) <= CONFIG["context_budget"]:
                cards = tentative
                accepted.append(key)
                if len(accepted) == CONFIG["context_max_seeds"]:
                    break
        used = self.tokens(cards)
        if used > CONFIG["context_budget"]:
            raise LiteratureError("Context budget exceeded")
        return {
            "cards": cards,
            "accepted_seed_ids": accepted,
            "reference_tokens_used": used,
            "budget": CONFIG["context_budget"],
            "tokenizer": self.encoder.name,
            "expanded": expand,
            "model_billing_tokens": None,
        }


def aggregate(rows: list[dict]) -> dict:
    result = {}
    for name in VARIANTS:

        def means(subset, name=name):
            scored = [r["variants"][name] for r in subset if r["scored"]]
            return {
                "scored_count": len(scored),
                **(
                    {
                        k: sum(v["metrics"][k] for v in scored) / len(scored)
                        for k in scored[0]["metrics"]
                    }
                    if scored
                    else {}
                ),
                "budget": {
                    k: sum(v["budget_metrics"][k] for v in scored) / len(scored)
                    for k in scored[0]["budget_metrics"]
                }
                if scored
                else {},
                "mean_reference_tokens": sum(
                    r["variants"][name]["context_pack"]["reference_tokens_used"] for r in subset
                )
                / len(subset)
                if subset
                else 0.0,
            }

        result[name] = means(rows)
        result[name]["per_category"] = {
            c: means([r for r in rows if r["category"] == c])
            for c in sorted({r["category"] for r in rows})
        }
    return result


def select_variant(summary: dict) -> str:
    return max(
        VARIANTS,
        key=lambda n: (
            summary[n]["budget"]["complete_evidence"],
            summary[n]["complete_evidence_at_5"],
            summary[n]["budget"]["required_evidence_coverage"],
            summary[n]["mrr_at_5"],
            -VARIANTS.index(n),
        ),
    )


def validate_selection(selection, dev, gold, revision, config):
    expected = {
        "domain": DOMAIN,
        "experiment_id": EXPERIMENT,
        "status": "complete",
        "split": "dev",
        "corpus_fingerprint": gold.corpus_fingerprint,
        "suite_fingerprint": gold.suite_fingerprint,
        "test_seal": gold.test_seal,
        "embedding_revision": revision,
        "config": config,
    }
    if any(dev.get(k) != v for k, v in expected.items()):
        raise LiteratureError("V3 development configuration differs; holdout remains closed")
    if selection != {
        "dev_report_fingerprint": digest(dev),
        "config_fingerprint": digest(config),
        "test_seal": gold.test_seal,
        "variant": select_variant(dev["summary"]),
    }:
        raise LiteratureError("V3 development selection differs")


def finite_scores(candidates: list) -> None:
    if len({c.chunk_id for c in candidates}) != len(candidates) or any(
        not math.isfinite(c.score) for c in candidates
    ):
        raise LiteratureError("Duplicate or nonfinite medical candidates")
