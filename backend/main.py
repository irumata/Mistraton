import logging

import psycopg
from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Mistralethon API",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

@app.get("/api/health")
def health():
    """Liveness only. Use /api/db to check database readiness."""
    return {"status": "ok", "project": "Mistralethon"}

@app.get("/api/db")
def database_health():
    """Read the real application schema without exposing credentials or server details."""
    try:
        # libpq reads PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD individually.
        with psycopg.connect(connect_timeout=3, options="-c statement_timeout=3000") as conn:
            row = conn.execute(
                "SELECT name, (SELECT max(version) FROM mistralethon.schema_migrations) "
                "FROM mistralethon.app_meta WHERE id = TRUE"
            ).fetchone()
        if row is None or row[1] != 1:
            raise HTTPException(status_code=503, detail="Database schema is not ready")
        return {"status": "ok", "database": "connected", "project": row[0], "schema_version": row[1]}
    except psycopg.Error:
        logger.warning("Database readiness check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None
