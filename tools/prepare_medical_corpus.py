"""Extract the explicit first medical PDF batch with stable page/span provenance."""

import argparse
import json
from pathlib import Path

from rag_portfolio.evaluation.experiments import medical_path
from rag_portfolio.evaluation.medical_corpus import prepare_corpus


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=root / "eval/medical_rehab_v1/sources.json")
    parser.add_argument("--output", type=Path, default=root / ".local/medical_rehab_v1/corpus")
    args = parser.parse_args()
    sources_path = medical_path(root, args.sources, public=True)
    # Validate an output file, so the namespace root itself is never an output target.
    output = medical_path(root, args.output / "manifest.json").parent
    result = prepare_corpus(
        root / "doc", json.loads(sources_path.read_text(encoding="utf-8")), output
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("corpus_fingerprint", "page_count", "chunk_count", "flagged_pages")
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
