"""Explicit experiment namespaces; historical scene fixtures retain their original paths."""

from dataclasses import dataclass
from pathlib import Path

from rag_portfolio.literature_backup import LiteratureError

MEDICAL_EXPERIMENT = "medical_rehab_v1"
MEDICAL_DOMAIN = "medical_knowledge"


@dataclass(frozen=True)
class Experiment:
    id: str
    domain: str
    suite: str
    output: str
    collection_prefix: str


EXPERIMENTS = {
    "medical_rehab_v3": Experiment(
        "medical_rehab_v3",
        MEDICAL_DOMAIN,
        "eval/medical_rehab_v3/cases.json",
        ".local/medical_rehab_v3",
        "rag_eval_medical_rehab_v3_",
    ),
    "medical_rehab_v2": Experiment(
        "medical_rehab_v2",
        MEDICAL_DOMAIN,
        "eval/medical_rehab_v2/cases.json",
        ".local/medical_rehab_v2",
        "rag_eval_medical_rehab_v2_",
    ),
    "metaverse_dense_v1": Experiment(
        "metaverse_dense_v1",
        "metaverse_scenes",
        "eval/hospital_dense_v1/cases.json",
        ".local/eval/hospital_dense_v1",
        "rag_eval_yueyang_dense_v1",
    ),
    "metaverse_v2": Experiment(
        "metaverse_v2",
        "metaverse_scenes",
        "eval/yueyang_multidoc_v2.json",
        ".local/eval/multidoc_eval_v2",
        "rag_eval_yueyang_dense_v1",
    ),
    MEDICAL_EXPERIMENT: Experiment(
        MEDICAL_EXPERIMENT,
        MEDICAL_DOMAIN,
        "eval/medical_rehab_v1/cases.json",
        ".local/medical_rehab_v1",
        "rag_eval_medical_rehab_v1_",
    ),
}


def medical_path(root: Path, path: Path, *, public: bool = False) -> Path:
    resolved = path.resolve()
    base = (root / ("eval/medical_rehab_v1" if public else ".local/medical_rehab_v1")).resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise LiteratureError(
            "Medical inputs/outputs must stay in the medical experiment namespace"
        )
    return resolved
