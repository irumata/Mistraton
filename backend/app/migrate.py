"""Apply ordered SQL migrations once, transactionally and with a PostgreSQL lock."""
from pathlib import Path

from app.db import connection


def main():
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(643178934)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
        applied = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted((Path(__file__).parent.parent / "migrations").glob("*.sql")):
            if path.name not in applied:
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                print(f"Applied {path.name}")


if __name__ == "__main__":
    main()
