"""Run integration tests in a newly allocated local database, then remove only that database."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from rag_portfolio.config import Settings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    settings = Settings()
    if settings.environment == "production" or settings.postgres_host not in {
        "127.0.0.1",
        "localhost",
    }:
        raise SystemExit("This runner only creates test databases on local development PostgreSQL.")
    database = "rag_test_" + uuid4().hex[:16]
    env = {
        **os.environ,
        "RAG_ENVIRONMENT": "test",
        "RAG_POSTGRES_DATABASE": database,
        "RAG_TEST_DATABASE": database,
    }
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        dbname=settings.postgres_database,
        autocommit=True,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        print(f"Allocated {database}", flush=True)
        try:
            for operation in [
                ("upgrade", "head"),
                ("downgrade", "base"),
                ("upgrade", "head"),
                ("check",),
            ]:
                migration = subprocess.run(
                    [sys.executable, "-m", "alembic", *operation], cwd=root, env=env, check=False
                )
                if migration.returncode:
                    return migration.returncode
            return subprocess.run(
                [sys.executable, "-m", "pytest", *sys.argv[1:]], cwd=root, env=env, check=False
            ).returncode
        finally:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )
            print(f"Removed owned test database {database}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
