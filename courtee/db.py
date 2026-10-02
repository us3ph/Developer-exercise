from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
import sqlite3
from typing import Iterator


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration "
                "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            applied = {row[0] for row in connection.execute("SELECT name FROM schema_migration")}
            migrations = sorted(files("courtee").joinpath("migrations").iterdir(), key=lambda p: p.name)
            for migration in migrations:
                if migration.name.endswith(".sql") and migration.name not in applied:
                    name = migration.name.replace("'", "''")
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8")
                        + f"\nINSERT INTO schema_migration(name) VALUES ('{name}');\nCOMMIT;"
                    )

    def seed(self) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO person(id, name, role) VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING",
                [(1, "Youssef", "client"), (2, "Sara", "client"), (3, "K. Bennani", "bank_adviser")],
            )
            connection.executemany(
                "INSERT INTO contact(id, person_id, channel, normalized_value) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                [(1, 1, "whatsapp", "+212661234567"), (2, 2, "whatsapp", "+212662000111"),
                 (3, 3, "email", "k.bennani@cihbank.ma")],
            )
            connection.executemany(
                "INSERT INTO dossier(id, reference, status, bank, active) "
                "VALUES (?, ?, 'submitted', ?, 1) ON CONFLICT(id) DO NOTHING",
                [(1, "D-1234", "CIH Bank"), (2, "D-2041", "CIH Bank"),
                 (3, "D-2077", "Banque fictive")],
            )
            connection.executemany(
                "INSERT INTO dossier_participant(person_id, dossier_id, role) "
                "VALUES (?, ?, ?) ON CONFLICT(person_id, dossier_id) DO NOTHING",
                [(1, 1, "borrower"), (2, 2, "borrower"), (2, 3, "borrower"),
                 (3, 1, "bank_adviser"), (3, 2, "bank_adviser")],
            )
            connection.executemany(
                "INSERT INTO message(channel, direction, external_id, content, sender, "
                "recipients_json, dossier_id, routing_method, routing_state, routing_reason, date, received_at) "
                "VALUES (?, 'outgoing', ?, ?, ?, ?, ?, 'seed', 'attached', 'Seeded outgoing message', ?, ?) "
                "ON CONFLICT(channel, external_id) DO NOTHING",
                [("whatsapp", "wamid.OUT-2041", "Des nouvelles du dossier D-2041 ?", "courtee",
                  '["+212662000111"]', 2, "2025-10-01T09:00:00+00:00", "2025-10-01T09:00:00+00:00"),
                 ("email", "<out-1234@dossiers.courtee.ai>", "Dossier D-1234 pour étude", "contact@courtee.ai",
                  '["k.bennani@cihbank.ma"]', 1, "2025-10-01T09:01:00+00:00", "2025-10-01T09:01:00+00:00")],
            )
