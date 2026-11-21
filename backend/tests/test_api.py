import os
from unittest.mock import MagicMock

import psycopg
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_health():
    assert client.get('/api/health').json() == {'status': 'ok', 'project': 'Mistralethon'}


def test_database_failure_is_safe(monkeypatch):
    def unavailable(**kwargs):
        raise psycopg.OperationalError('password=TOP_SECRET internal-host')
    monkeypatch.setattr(main.psycopg, 'connect', unavailable)
    response = client.get('/api/db')
    assert response.status_code == 503
    assert response.json() == {'detail': 'Database unavailable'}
    assert 'TOP_SECRET' not in response.text


def test_missing_schema_row(monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr(main.psycopg, 'connect', lambda **kwargs: connection)
    assert client.get('/api/db').status_code == 503


def test_live_database_when_requested():
    if os.environ.get('TEST_DATABASE') != '1':
        import pytest
        pytest.skip('Set TEST_DATABASE=1 and PG* variables for a real PostgreSQL test')
    response = client.get('/api/db')
    assert response.status_code == 200
    assert response.json() == {
        'status': 'ok', 'database': 'connected',
        'project': 'Mistralethon', 'schema_version': 1,
    }
    with psycopg.connect() as conn:
        superuser = conn.execute('SELECT rolsuper FROM pg_roles WHERE rolname = current_user').fetchone()[0]
        assert superuser is False
