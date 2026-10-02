"""Logging outside the pure router; no provider call is made.

The Step 3 ingestion service should call log_dossier_choice only after a newly
received message has been saved, so webhook replays cannot repeat this log.
"""
import json
import logging

from courtee.domain import NormalizedMessage, RoutingDecision

logger = logging.getLogger(__name__)


def log_dossier_choice(message: NormalizedMessage, decision: RoutingDecision) -> None:
    if message.channel != "whatsapp" or decision.state != "needs_choice":
        return
    payload = {
        "messaging_product": "whatsapp",
        "to": message.sender.removeprefix("+"),
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": "Quel dossier concerne votre message ?"},
            "action": {
                "button": "Choisir un dossier",
                "sections": [{
                    "title": "Vos dossiers",
                    "rows": [{"id": ref, "title": ref} for ref in decision.candidates],
                }],
            },
        },
    }
    logger.info(
        "Simulated WhatsApp dossier choice for %s: %s",
        message.external_id, json.dumps(payload, ensure_ascii=False),
        extra={"source_external_id": message.external_id, "interactive_payload": payload},
    )
