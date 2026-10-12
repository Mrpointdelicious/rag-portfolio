"""Local candidate release CLI. Never touches the production API or database."""

import argparse
import asyncio
from pathlib import Path

from rag_portfolio.medical_release import ReleaseRegistry, load
from rag_portfolio.public_medical import immutable_json
from rag_portfolio.public_medical_retrieval import build_index


async def main(args):
    registry = ReleaseRegistry(args.registry)
    if args.command == "index":
        corpus = load(args.corpus)
        approval = load(args.approval) if args.approval else None
        index = await build_index(corpus, args.index, processing_approval=approval)
        immutable_json(args.index, index)
        immutable_json(
            args.manifest,
            {k: v for k, v in index.items() if k != "rows"} | {"chunk_count": len(index["rows"])},
        )
        print({"index_sha256": index["index_sha256"], "chunks": len(index["rows"])})
    elif args.command == "register":
        registry.register(args.release_id, args.corpus, args.index, args.approval)
    elif args.command == "show":
        state = registry.read()
        print(
            {
                "generation": state["generation"],
                "active": state["active"],
                "releases": {k: v["status"] for k, v in state["releases"].items()},
            }
        )
    else:
        registry.transition(args.release_id, args.command)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "command",
        choices=["index", "register", "validate", "activate", "revoke", "rollback", "show"],
    )
    p.add_argument(
        "--registry", type=Path, default=Path(".local/medical_business_v1/releases/registry.json")
    )
    p.add_argument("--release-id", default="combined-business-v1")
    p.add_argument("--corpus", type=Path)
    p.add_argument("--index", type=Path)
    p.add_argument("--approval", type=Path)
    p.add_argument(
        "--manifest", type=Path, default=Path(".local/medical_business_v1/index_manifest.json")
    )
    asyncio.run(main(p.parse_args()))
