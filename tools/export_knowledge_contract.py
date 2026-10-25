"""Export the public OpenAPI contract; optionally copy it into an Agent checkout."""

import argparse
import json
from pathlib import Path

from rag_portfolio.config import Settings
from rag_portfolio.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def contract():
    schema = create_app(Settings(_env_file=None)).openapi()
    selected = {"/v1/knowledge/search": schema["paths"]["/v1/knowledge/search"]}
    names = set()

    def collect(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in names:
                    names.add(name)
                    collect(schema["components"]["schemas"][name])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(selected)
    return {
        "openapi": "3.1.0",
        "info": {"title": "Knowledge Search", "version": "1.0.0"},
        "paths": selected,
        "components": {
            "schemas": {name: schema["components"]["schemas"][name] for name in sorted(names)},
            "securitySchemes": schema["components"]["securitySchemes"],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-root", type=Path)
    args = parser.parse_args()
    targets = [ROOT / "contracts" / "knowledge-search-v1.openapi.json"]
    if args.agent_root:
        agent = args.agent_root.resolve()
        if not (agent / "src/meta_agent/config.py").is_file():
            parser.error("--agent-root must name a metaAgent checkout")
        targets.append(agent / "src/meta_agent/integrations/knowledge-search-v1.openapi.json")
    data = json.dumps(contract(), ensure_ascii=False, indent=2) + "\n"
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")
        print(target)


if __name__ == "__main__":
    main()
