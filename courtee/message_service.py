"""Database lookups and persistence around the pure routing function."""
from dataclasses import asdict, dataclass
import json
import sqlite3
from typing import Any

from courtee.analysis_service import AnalysisService, dossier_analysis_view, message_analysis_view
from courtee.analyzers import Analyzer, FakeAnalyzer
from courtee.db import Database
from courtee.domain import Dossier, KnownMessage, LookupFacts, NormalizedMessage
from courtee.routing import route
from courtee.routing_service import log_dossier_choice

MESSAGE_SELECT = """
    SELECT m.*, d.reference AS dossier_reference
    FROM message m LEFT JOIN dossier d ON d.id = m.dossier_id
"""


class DossierNotFound(LookupError):
    pass


@dataclass(frozen=True)
class IngestionResult:
    message: dict[str, Any]
    duplicate: bool


def _saved_message(row: sqlite3.Row, connection: sqlite3.Connection) -> dict[str, Any]:
    message = dict(row)
    for field in ("recipients", "headers", "attachments", "candidates"):
        message[field] = json.loads(message.pop(f"{field}_json"))
    # Keep the provider payload in the database without repeating it in every read.
    message.pop("payload_json")
    message.update(message_analysis_view(connection, message["id"]))
    return message


class MessageService:
    def __init__(self, database: Database, analyzer: Analyzer | None = None):
        self.database = database
        self.analysis = AnalysisService(database, analyzer if analyzer is not None else FakeAnalyzer())

    def _existing(self, connection: sqlite3.Connection, channel: str, external_id: str) -> dict | None:
        row = connection.execute(
            MESSAGE_SELECT + " WHERE m.channel = ? AND m.external_id = ?", (channel, external_id),
        ).fetchone()
        return _saved_message(row, connection) if row is not None else None

    def _lookup_facts(self, connection: sqlite3.Connection, message: NormalizedMessage) -> LookupFacts:
        person = connection.execute(
            "SELECT p.id FROM contact c JOIN person p ON p.id = c.person_id "
            "WHERE c.channel = ? AND c.normalized_value = ?", (message.channel, message.sender),
        ).fetchone()
        sender_dossier_ids = frozenset(
            row[0] for row in connection.execute(
                "SELECT dossier_id FROM dossier_participant WHERE person_id = ?", (person[0],),
            )
        ) if person is not None else frozenset()
        dossiers = tuple(Dossier(row["id"], row["reference"], bool(row["active"]))
                         for row in connection.execute("SELECT id, reference, active FROM dossier"))
        outgoing = tuple(KnownMessage(row["channel"], row["direction"], row["external_id"], row["dossier_id"])
                         for row in connection.execute(
                             "SELECT channel, direction, external_id, dossier_id FROM message "
                             "WHERE channel = ? AND direction = 'outgoing' AND dossier_id IS NOT NULL",
                             (message.channel,),
                         ))
        return LookupFacts(dossiers, person is not None, sender_dossier_ids, outgoing)

    def ingest(self, message: NormalizedMessage) -> IngestionResult:
        with self.database.connect() as connection:
            # Serialize writers so the lookup snapshot and saved decision agree.
            connection.execute("BEGIN IMMEDIATE")
            existing = self._existing(connection, message.channel, message.external_id)
            if existing is not None:
                return IngestionResult(existing, duplicate=True)

            decision = route(message, self._lookup_facts(connection, message))
            inserted = connection.execute(
                """
                INSERT INTO message (
                    channel, direction, external_id, content, sender, recipients_json, subject,
                    headers_json, attachments_json, payload_json, dossier_id, routing_method,
                    routing_state, routing_reason, candidates_json, date, received_at
                ) VALUES (?, 'incoming', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, external_id) DO NOTHING
                RETURNING id
                """,
                (message.channel, message.external_id, message.content, message.sender,
                 json.dumps(message.recipients), message.subject, json.dumps(message.headers),
                 json.dumps([asdict(item) for item in message.attachments]),
                 json.dumps(message.payload, ensure_ascii=False), decision.dossier_id,
                 decision.method, decision.state, decision.reason, json.dumps(decision.candidates),
                 message.date, message.received_at),
            ).fetchone()
            # The database constraint is authoritative even if the advisory read missed a replay.
            if inserted is None:
                existing = self._existing(connection, message.channel, message.external_id)
                if existing is None:
                    raise RuntimeError("Message insert returned no saved row")
                return IngestionResult(existing, duplicate=True)

            context = self.analysis.prepare(connection, inserted["id"], decision.dossier_id, message)
            saved = _saved_message(connection.execute(
                MESSAGE_SELECT + " WHERE m.id = ?", (inserted["id"],),
            ).fetchone(), connection)

        # Only a successful, committed new insert may produce side effects.
        log_dossier_choice(message, decision)
        if context is not None:
            self.analysis.process(context)
            with self.database.connect() as connection:
                saved = self._existing(connection, message.channel, message.external_id)
        return IngestionResult(saved, duplicate=False)

    def dossier_thread(self, reference: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            dossier = connection.execute("SELECT * FROM dossier WHERE reference = ?", (reference,)).fetchone()
            if dossier is None:
                raise DossierNotFound(reference)
            messages = [_saved_message(row, connection) for row in connection.execute(
                MESSAGE_SELECT + " WHERE m.dossier_id = ? ORDER BY m.date DESC, m.id DESC", (dossier["id"],),
            )]
            details = dict(dossier)
            details["active"] = bool(details["active"])
            return {"dossier": details, "messages": messages, **dossier_analysis_view(connection, dossier["id"])}

    def triage(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            messages = [_saved_message(row, connection) for row in connection.execute(
                MESSAGE_SELECT + " WHERE m.dossier_id IS NULL ORDER BY m.date DESC, m.id DESC",
            )]
            return {"messages": messages}
