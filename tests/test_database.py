import sqlite3

import pytest


def test_migrations_and_seed_are_repeatable(database):
    database.migrate()
    database.seed()
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM person").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM dossier_participant").fetchone()[0] == 5
        assert connection.execute("SELECT count(*) FROM message").fetchone()[0] == 2
        sara = connection.execute(
            "SELECT d.reference FROM contact c JOIN dossier_participant dp ON dp.person_id = c.person_id "
            "JOIN dossier d ON d.id = dp.dossier_id WHERE c.normalized_value = ? ORDER BY d.reference",
            ("+212662000111",),
        ).fetchall()
        assert [row[0] for row in sara] == ["D-2041", "D-2077"]


def test_contact_participant_links_and_external_ids_are_constrained(database):
    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO dossier_participant VALUES (2, 2, 'borrower')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE message SET external_id = 'wamid.OUT-2041', channel = 'whatsapp'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO contact VALUES (99, 999, 'email', 'nobody@example.test')")


def test_all_required_fixtures_exist_and_have_external_ids(load_fixture):
    for case in "ABCDEFGHIJKL":
        payload = load_fixture(case)
        assert payload.get("id") or payload["headers"]["Message-ID"]
