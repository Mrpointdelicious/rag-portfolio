"""Select an explicit domain/experiment; keep historical metaverse entrypoints intact."""

import argparse
import subprocess
import sys
from pathlib import Path

from rag_portfolio.evaluation.experiments import EXPERIMENTS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", choices=tuple(EXPERIMENTS))
    args, forwarded = parser.parse_known_args()
    scripts = {
        "metaverse_dense_v1": "eval_retrieval.py",
        "metaverse_v2": "eval_retrieval_v2.py",
        "medical_rehab_v1": "eval_medical.py",
        "medical_rehab_v2": "eval_medical_v2.py",
        "medical_rehab_v3": "eval_medical_v3.py",
    }
    root = Path(__file__).resolve().parents[1]
    print(
        f"Domain: {EXPERIMENTS[args.experiment].domain}; experiment: {args.experiment}", flush=True
    )
    return subprocess.run(
        [sys.executable, str(root / "tools" / scripts[args.experiment]), *forwarded],
        cwd=root,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
