"""Idempotent schema setup, run by Compose before the API starts."""
import os
from pathlib import Path

import psycopg
from psycopg import sql


def migrate():
    password = os.environ["APP_DB_PASSWORD"]
    with psycopg.connect(connect_timeout=10) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(62973117)")
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", ("app",)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(verb + " ROLE app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}")
            .format(sql.Literal(password))
        )
        conn.execute("CREATE SCHEMA IF NOT EXISTS mistralethon AUTHORIZATION app")
        conn.execute("SET LOCAL ROLE app")
        conn.execute((Path(__file__).parent / "schema.sql").read_text())
    print("Mistralethon schema version 1 is ready")


if __name__ == "__main__":
    migrate()
