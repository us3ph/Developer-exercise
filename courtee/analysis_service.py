from datetime import datetime, timezone
import json
import logging
import sqlite3

import httpx
from pydantic import TypeAdapter

from courtee.analysis_contract import (
    Action, AnalysisAttachment, AnalysisContext, AttachmentAction, DocumentAction,
    StatusAction, validate_analysis,
)
from courtee.analyzers import Analyzer
from courtee.db import Database
from courtee.domain import NormalizedMessage

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def proposal_view(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["action"] = json.loads(value.pop("action_json"))
    return value


def history_view(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["action"] = json.loads(value.pop("action_json"))
    value["changes"] = json.loads(value.pop("changes_json"))
    return value


def message_analysis_view(connection: sqlite3.Connection, message_id: int) -> dict:
    row = connection.execute("SELECT * FROM message_analysis WHERE message_id = ?", (message_id,)).fetchone()
    analysis = None
    if row is not None:
        analysis = {"status": row["status"], "error": row["error"],
                    "result": json.loads(row["result_json"]) if row["result_json"] else None}
    proposals = [proposal_view(row) for row in connection.execute(
        "SELECT * FROM proposal WHERE message_id = ? ORDER BY action_index", (message_id,),
    )]
    classifications = [dict(row) for row in connection.execute(
        "SELECT id AS attachment_id, external_id, filename, mime_type, classification FROM attachment "
        "WHERE message_id = ? AND classification IS NOT NULL ORDER BY position", (message_id,),
    )]
    return {"analysis": analysis, "proposals": proposals, "attachment_classifications": classifications}


def dossier_analysis_view(connection: sqlite3.Connection, dossier_id: int) -> dict:
    return {
        "history": [history_view(row) for row in connection.execute(
            "SELECT * FROM dossier_history WHERE dossier_id = ? ORDER BY created_at DESC, id DESC", (dossier_id,),
        )],
        "document_requests": [dict(row) for row in connection.execute(
            "SELECT * FROM document_request WHERE dossier_id = ? ORDER BY created_at DESC, id DESC", (dossier_id,),
        )],
    }


def _check_attachment(connection: sqlite3.Connection, action: AttachmentAction, message_id: int):
    row = connection.execute("SELECT * FROM attachment WHERE id = ? AND message_id = ?",
                             (action.attachment_id, message_id)).fetchone()
    if row is None:
        raise ValueError("Classification action must target an attachment from the source message")
    return row


def _apply_action(connection: sqlite3.Connection, action: Action, dossier_id: int,
                  message_id: int, action_index: int, applied_by: str, proposal_id: int | None = None) -> dict:
    timestamp = _now()
    if isinstance(action, StatusAction):
        previous = connection.execute("SELECT status FROM dossier WHERE id = ?", (dossier_id,)).fetchone()[0]
        connection.execute("UPDATE dossier SET status = ? WHERE id = ?", (action.value, dossier_id))
        changes = {"status": {"before": previous, "after": action.value}}
    elif isinstance(action, DocumentAction):
        request = connection.execute(
            "INSERT INTO document_request(dossier_id, message_id, action_index, document, created_at) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id", (dossier_id, message_id, action_index, action.value, timestamp),
        ).fetchone()
        changes = {"document_request_id": request[0], "requested_document": action.value}
    else:
        attachment = _check_attachment(connection, action, message_id)
        connection.execute("UPDATE attachment SET classification = ? WHERE id = ?", (action.value, attachment["id"]))
        changes = {"attachment_id": attachment["id"],
                   "classification": {"before": attachment["classification"], "after": action.value}}
    row = connection.execute(
        "INSERT INTO dossier_history(dossier_id, message_id, action_index, proposal_id, action_json, "
        "changes_json, applied_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *",
        (dossier_id, message_id, action_index, proposal_id, action.model_dump_json(),
         json.dumps(changes), applied_by, timestamp),
    ).fetchone()
    return history_view(row)


class AnalysisService:
    def __init__(self, database: Database, analyzer: Analyzer):
        self.database = database
        self.analyzer = analyzer

    def prepare(self, connection: sqlite3.Connection, message_id: int,
                dossier_id: int | None, message: NormalizedMessage) -> AnalysisContext | None:
        attachments = []
        for position, attachment in enumerate(message.attachments):
            row = connection.execute(
                "INSERT INTO attachment(message_id, position, external_id, filename, mime_type) "
                "VALUES (?, ?, ?, ?, ?) RETURNING id",
                (message_id, position, attachment.external_id, attachment.filename, attachment.mime_type),
            ).fetchone()
            attachments.append(AnalysisAttachment(row[0], attachment.external_id, attachment.filename, attachment.mime_type))
        if dossier_id is None:
            return None
        timestamp = _now()
        connection.execute("INSERT INTO message_analysis(message_id, status, created_at, updated_at) "
                           "VALUES (?, 'pending', ?, ?)", (message_id, timestamp, timestamp))
        dossier = connection.execute("SELECT reference, status FROM dossier WHERE id = ?", (dossier_id,)).fetchone()
        return AnalysisContext(message_id, dossier_id, dossier["reference"], dossier["status"],
                               message.channel, message.content, message.subject, tuple(attachments))

    def process(self, context: AnalysisContext) -> None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            claimed = connection.execute(
                "UPDATE message_analysis SET status = 'processing', updated_at = ? "
                "WHERE message_id = ? AND status = 'pending' RETURNING message_id", (_now(), context.message_id),
            ).fetchone()
            if claimed is None:
                return
        raw = None
        try:
            # The received message and claim are committed; no database lock spans the API call.
            raw = self.analyzer.analyze(context)
            result = validate_analysis(raw)
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                # Check every attachment target before applying any action.
                for action in result.actions:
                    if isinstance(action, AttachmentAction):
                        _check_attachment(connection, action, context.message_id)
                for index, action in enumerate(result.actions):
                    if action.risk == "low" and result.confidence >= 0.85:
                        _apply_action(connection, action, context.dossier_id, context.message_id, index, "auto")
                    else:
                        connection.execute(
                            "INSERT INTO proposal(message_id, dossier_id, action_index, action_json, confidence, "
                            "status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                            (context.message_id, context.dossier_id, index, action.model_dump_json(), result.confidence, _now()),
                        )
                connection.execute("UPDATE message_analysis SET status = 'completed', raw_output = ?, "
                                   "result_json = ?, error = NULL, updated_at = ? WHERE message_id = ?",
                                   (raw, result.model_dump_json(), _now(), context.message_id))
        except Exception as error:
            # Failed application transactions roll back before we mark manual review.
            reason = f"Analysis failed ({type(error).__name__})"
            if isinstance(error, httpx.HTTPStatusError):
                reason = f"Analyzer provider returned HTTP {error.response.status_code}"
            with self.database.connect() as connection:
                connection.execute("UPDATE message_analysis SET status = 'manual_review', raw_output = ?, "
                                   "result_json = NULL, error = ?, updated_at = ? WHERE message_id = ?",
                                   (raw if isinstance(raw, str) else None, reason, _now(), context.message_id))
            logger.warning("Message %s requires manual review: %s", context.message_id, reason)


class ProposalNotFound(LookupError):
    pass


class ProposalNotPending(ValueError):
    pass


class ProposalService:
    def __init__(self, database: Database):
        self.database = database

    def review(self, proposal_id: int, reviewer: str, *, accept: bool) -> dict:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
            if row is None:
                raise ProposalNotFound(proposal_id)
            if row["status"] != "pending":
                raise ProposalNotPending("Proposal has already been reviewed")
            history = None
            if accept:
                action = TypeAdapter(Action).validate_json(row["action_json"])
                history = _apply_action(connection, action, row["dossier_id"], row["message_id"],
                                        row["action_index"], reviewer, row["id"])
            updated = connection.execute(
                "UPDATE proposal SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ? RETURNING *",
                ("applied" if accept else "rejected", reviewer, _now(), proposal_id),
            ).fetchone()
            return {"proposal": proposal_view(updated), "history": history}
