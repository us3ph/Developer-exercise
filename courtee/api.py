from contextlib import asynccontextmanager
import logging
import os
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from courtee.analysis_contract import Action, AnalysisResult
from courtee.analysis_service import ProposalNotFound, ProposalNotPending, ProposalService
from courtee.analyzers import Analyzer, analyzer_from_environment
from courtee.db import Database
from courtee.domain import Attachment, Channel, RoutingState
from courtee.message_service import DossierNotFound, MessageService
from courtee.normalization import EmailPayload, WhatsAppPayload, normalize_email, normalize_whatsapp


class AnalysisView(BaseModel):
    status: Literal["pending", "processing", "completed", "manual_review"]
    result: AnalysisResult | None
    error: str | None


class ProposalView(BaseModel):
    id: int
    message_id: int
    dossier_id: int
    action_index: int
    action: Action
    confidence: float
    status: Literal["pending", "applied", "rejected"]
    reviewed_by: str | None
    reviewed_at: str | None
    created_at: str


class AttachmentClassificationView(BaseModel):
    attachment_id: int
    external_id: str
    filename: str
    mime_type: str
    classification: str


class HistoryView(BaseModel):
    id: int
    dossier_id: int
    message_id: int
    action_index: int
    proposal_id: int | None
    action: Action
    changes: dict[str, Any]
    applied_by: str
    created_at: str


class DocumentRequestView(BaseModel):
    id: int
    dossier_id: int
    message_id: int
    action_index: int
    document: str
    created_at: str


class ReviewerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: StrictStr = Field(min_length=1, max_length=200)

    @field_validator("reviewer")
    @classmethod
    def reviewer_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value or value.casefold() == "auto":
            raise ValueError("A human reviewer identifier is required")
        return value


class ReviewResponse(BaseModel):
    proposal: ProposalView
    history: HistoryView | None


class MessageView(BaseModel):
    id: int
    channel: Channel
    direction: Literal["incoming", "outgoing"]
    external_id: str
    content: str
    sender: str
    recipients: list[str]
    subject: str
    headers: dict[str, str]
    attachments: list[Attachment]
    dossier_id: int | None
    dossier_reference: str | None
    routing_method: str
    routing_state: RoutingState
    routing_reason: str
    candidates: list[str]
    date: str
    received_at: str
    analysis: AnalysisView | None
    proposals: list[ProposalView]
    attachment_classifications: list[AttachmentClassificationView]


class IngestionResponse(BaseModel):
    message: MessageView
    duplicate: bool


class DossierView(BaseModel):
    id: int
    reference: str
    status: str
    bank: str
    active: bool


class ThreadResponse(BaseModel):
    dossier: DossierView
    messages: list[MessageView]
    history: list[HistoryView]
    document_requests: list[DocumentRequestView]


class TriageResponse(BaseModel):
    messages: list[MessageView]


def create_app(database: Database | None = None, analyzer: Analyzer | None = None) -> FastAPI:
    if analyzer is None:
        load_dotenv(".env", override=False)
    database = database if database is not None else Database(os.environ.get("COURTEE_DB", "data/courtee.sqlite3"))
    service = MessageService(database, analyzer)
    proposals = ProposalService(database)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        database.migrate()
        if analyzer is None:
            service.analysis.analyzer = analyzer_from_environment()
        yield

    app = FastAPI(title="Courtee", version="0.1.0", lifespan=lifespan)
    app.state.message_service = service

    @app.post("/webhooks/whatsapp", response_model=IngestionResponse, status_code=201,
              responses={200: {"model": IngestionResponse, "description": "Previously saved message"}})
    def whatsapp_webhook(payload: WhatsAppPayload, response: Response):
        try:
            message = normalize_whatsapp(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        result = service.ingest(message)
        response.status_code = 200 if result.duplicate else 201
        return {"message": result.message, "duplicate": result.duplicate}

    @app.post("/webhooks/email", response_model=IngestionResponse, status_code=201,
              responses={200: {"model": IngestionResponse, "description": "Previously saved message"}})
    def email_webhook(payload: EmailPayload, response: Response):
        try:
            message = normalize_email(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        result = service.ingest(message)
        response.status_code = 200 if result.duplicate else 201
        return {"message": result.message, "duplicate": result.duplicate}

    @app.get("/dossiers/{reference}/messages", response_model=ThreadResponse)
    def dossier_messages(reference: str):
        try:
            return service.dossier_thread(reference)
        except DossierNotFound as error:
            raise HTTPException(status_code=404, detail="Dossier not found") from error

    @app.get("/triage", response_model=TriageResponse)
    def triage_messages():
        return service.triage()

    def review_proposal(proposal_id: int, request: ReviewerRequest, *, accept: bool):
        try:
            return proposals.review(proposal_id, request.reviewer, accept=accept)
        except ProposalNotFound as error:
            raise HTTPException(status_code=404, detail="Proposal not found") from error
        except ProposalNotPending as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/proposals/{proposal_id}/validate", response_model=ReviewResponse)
    def validate_proposal(proposal_id: int, request: ReviewerRequest):
        return review_proposal(proposal_id, request, accept=True)

    @app.post("/proposals/{proposal_id}/reject", response_model=ReviewResponse)
    def reject_proposal(proposal_id: int, request: ReviewerRequest):
        return review_proposal(proposal_id, request, accept=False)

    return app


app = create_app()
