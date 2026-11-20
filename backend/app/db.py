import psycopg
from psycopg.rows import dict_row

from app.config import settings


def connection():
    # Short-lived connections: no process-local state or shared connection across forks.
    return psycopg.connect(settings().database_url, connect_timeout=5, row_factory=dict_row)
