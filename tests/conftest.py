import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from courtee.api import create_app
from courtee.analyzers import FakeAnalyzer
from courtee.db import Database


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    db.seed()
    return db


@pytest.fixture
def load_fixture():
    def load(case):
        return json.loads((Path(__file__).parents[1] / "fixtures" / f"{case}.json").read_text())
    return load


@pytest.fixture
def client(database):
    with TestClient(create_app(database, analyzer=FakeAnalyzer())) as test_client:
        yield test_client
