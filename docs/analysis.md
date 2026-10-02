# Analysis and proposal review

The app loads a local `.env` without overwriting exported environment variables. `.env` is ignored by Git. Tests inject `FakeAnalyzer`, so they never use the local API key or make live requests.

`COURTEE_ANALYZER=fake` is the default. To enable OpenRouter, add `COURTEE_ANALYZER=llm` alongside the existing `OPENROUTER_API_KEY` in `.env`. `OPENROUTER_MODEL` defaults to `openrouter/free` and can select another model explicitly. Install the updated project dependencies before starting the app.

The live adapter uses OpenRouter's [chat completions endpoint](https://openrouter.ai/docs/quickstart), a JSON-only system prompt, and `response_format: {"type": "json_object"}`. The [free router](https://openrouter.ai/openrouter/free) selects free models supporting the requested features. Model availability and quotas depend on OpenRouter; consult its [limits](https://openrouter.ai/docs/api_reference/limits). There is no paid-model fallback configured in this application.

Every newly attached incoming message is saved before analysis. Attachment metadata and a pending analysis row are saved in the same ingestion transaction. A separate transaction claims the analysis, then releases the database lock before the analyzer runs. Replays return the saved message and never repeat analyzer calls or applied actions, including while the first request is still processing. Previous messages and seeded outgoing messages are not analyzed retroactively.

The output validator requires `intents`, `entities`, `confidence`, `actions`, and `reply_draft`. It rejects malformed JSON, duplicate object keys, nonfinite numbers, invalid confidence, unsupported actions/risks, and extra fields. All attachment targets are checked against the source message before any action is applied. Invalid results and provider failures leave the message attached with `analysis.status = manual_review`; no proposals or actions are created from failed output.

Low-risk actions with confidence at least `0.85` apply automatically. Every other valid action becomes a pending proposal. Automatic action application, pending proposal creation, result storage, and applied-action history use one transaction, so an application failure rolls back the entire action batch before the analysis is marked for manual review.

Supported actions:

- `set_status`: `value` is the new dossier status. FakeAnalyzer proposes `accord_principe` at medium risk when the text contains an agreement in principle.
- `request_document`: `value` is the requested document category. Application creates a document-request record and history.
- `classify_attachment`: `value` is the category and `attachment_id` identifies one of the source message's persisted attachments. This action name and extra ID are internal choices for the required classification behavior. The fake analyzer recognizes salary certificates from their simulated filenames; other filenames receive `document`.

`reply_draft` is retained in the analysis result. Classification uses simulated metadata; the application does not download or inspect real files.

Webhook responses and dossier messages expose `analysis`, `proposals`, and `attachment_classifications`. Proposal IDs are available at `message.proposals[].id`. `GET /dossiers/{ref}/messages` additionally exposes applied-action `history` and `document_requests`.

Both `POST /proposals/{id}/validate` and `POST /proposals/{id}/reject` accept `{"reviewer": "tutor-1"}`. The reviewer is an explicit identifier supplied by the exercise client; staff authentication is outside this exercise. Validation applies the action, records history with the reviewer and source message, and marks the proposal applied in one transaction. Rejection records the reviewer and marks the proposal rejected. Missing proposals return `404`; already reviewed proposals return `409`; missing/blank reviewer identifiers and reserved `auto` return `422`.

The analysis fixtures are `fixtures/analysis/attachment.json`, `agreement.json`, and `document_request.json`. Malformed output is simulated by injecting `FakeAnalyzer(output_override="{invalid JSON")` in tests; inbound message text cannot enable this override.
