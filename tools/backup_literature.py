"""Back up every inventoried original PDF, including unselected and method papers."""

import argparse
import json
from pathlib import Path

from rag_portfolio.literature_backup import backup_pdfs, inventory_from_workbook, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--inventory", type=Path, default=Path(".local/medical_rehab_v1/inventory.json")
    )
    source.add_argument("--workbook", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.workbook:
        inventory = inventory_from_workbook(args.workbook)
        write_json(root / ".local/medical_rehab_v1/inventory.json", inventory)
    else:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    result = backup_pdfs(inventory, root / "doc")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
