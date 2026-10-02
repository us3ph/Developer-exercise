from contextlib import asynccontextmanager
import logging
import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from courtee.db import Database
from courtee.domain import Attachment, Channel, RoutingState
from courtee.message_service import DossierNotFound, MessageService
from courtee.normalization import EmailPayload, WhatsAppPayload, normalize_email, normalize_whatsapp


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


class TriageResponse(BaseModel):
    messages: list[MessageView]


def create_app(database: Database | None = None) -> FastAPI:
    database = database if database is not None else Database(os.environ.get("COURTEE_DB", "data/courtee.sqlite3"))
    service = MessageService(database)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        database.migrate()
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

    return app


app = create_app()
