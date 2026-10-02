# Implementation choices

- Python 3.11+, FastAPI, standard-library SQLite, and pytest. No team stack was provided.
- Fixture-only defaults: every seeded dossier starts at `submitted`; D-2041 uses CIH Bank, and D-2077 uses `Banque fictive`. The brief specifies CIH for D-1234 and adviser Bennani, but does not specify Sara's second bank or initial statuses.
- IDs, dates, people, attachments, and messages are simulated. No messaging provider is contacted.
- WhatsApp fixtures use the simplified inbound `messages[]` object requested in the exercise. Meta documents `from`, `id`, `timestamp`, `type`, `text.body`, and inbound reply `context.id` in its [messages reference](https://www.postman.com/meta/whatsapp-business-platform/folder/1dtuocp/messages-object) and [Cloud API webhook examples](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api?entity=request-13382743-ba924e99-3d98-4954-b4e3-73a519939c33). Reviewed on 2026-10-02. `timestamp` is event time in Unix seconds; inbound `context.id` differs from outbound send `context.message_id`. Full webhook envelopes and status events are outside the required simplified adapter.
- The user's instruction overrides the plans' commit requirement: do not commit or push.
- Complete one step at a time, mark it finished, report the result, and stop. Start the next step only after the user's explicit confirmation.
