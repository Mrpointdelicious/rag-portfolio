"""Create isolated local credentials; never overwrite an existing environment file."""

from pathlib import Path
from secrets import token_urlsafe


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    if target.exists():
        print("Existing .env preserved.")
        return
    source = (root / ".env.example").read_text(encoding="utf-8")
    keys = {
        "RAG_POSTGRES_PASSWORD",
        "RAG_QDRANT_API_KEY",
        "RAG_SERVICE_TOKEN",
        "RAG_ADMIN_TOKEN",
    }
    lines = []
    for line in source.splitlines():
        key, separator, _ = line.partition("=")
        lines.append(f"{key}={token_urlsafe(36)}" if separator and key in keys else line)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    print("Created local .env with generated credentials. No credentials printed.")


if __name__ == "__main__":
    main()
