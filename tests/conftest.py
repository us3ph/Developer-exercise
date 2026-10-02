import json
from pathlib import Path

import pytest

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
