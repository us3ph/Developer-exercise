# Courtee

A Python/FastAPI backend that saves simulated WhatsApp messages and emails, routes them to mortgage dossiers, and analyzes attached messages with automatic actions or human review. SQLite stores messages, routing decisions, proposals, and action history.

## Start the project

Requirements: Python 3.11 or newer, `curl`, and a Bash-compatible shell. Run the commands from the repository root.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'

export COURTEE_DB=data/courtee.sqlite3
python -m courtee.cli init-db
COURTEE_ANALYZER=fake python -m uvicorn courtee.api:app --host 127.0.0.1 --port 8000
```

`init-db` applies both SQL migrations and loads the required people, contacts, three active dossiers, and two outgoing messages. It is safe to run again: existing messages and dossier statuses are preserved. API startup also applies migrations; seeding is explicit.

The server is at `http://127.0.0.1:8000`. Interactive API documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). Stop the server with `Ctrl+C`.

The command above selects `FakeAnalyzer`, even if your `.env` selects live analysis. It works without an API key. Seeded outgoing messages are retained as routing context and are not analyzed.

## Configure analysis

The application loads `.env` from the working directory. Exported environment variables take precedence. [.env.example](.env.example) provides a template; `.env` itself is ignored by Git.

| Variable | Purpose | Default |
| --- | --- | --- |
| `COURTEE_ANALYZER` | Select `fake` or `llm`. | `fake` |
| `OPENROUTER_API_KEY` | Authenticate live OpenRouter calls. | Required only in `llm` mode. |
| `OPENROUTER_MODEL` | Choose the live model. | `openrouter/free` |
| `COURTEE_DB` | Select the SQLite file. | `data/courtee.sqlite3` |

To use OpenRouter, edit `.env`:

```dotenv
COURTEE_ANALYZER=llm
OPENROUTER_API_KEY="your-openrouter-api-key"
OPENROUTER_MODEL=openrouter/free
```

Restart with `python -m uvicorn courtee.api:app --host 127.0.0.1 --port 8000` to use that configuration. Set `COURTEE_ANALYZER=fake` and restart to return to fake analysis. The API key and analyzer selector are separate settings.

The setup CLI reads exported variables, rather than loading `.env`. Export the same `COURTEE_DB` for setup and startup, as in the launch commands, or pass `--db` explicitly to `init-db`.

Newly attached messages are saved before analysis. Only actions with `risk=low` and `confidence >= 0.85` apply automatically; other valid actions become pending proposals. Invalid results or provider failures leave the message attached with `analysis.status=manual_review` and apply nothing. Live availability and output can vary; use the fake analyzer for the reproducible demo. See [the analysis contract and persistence details](docs/analysis.md).

## Run the tests

With the virtual environment active:

```bash
python -m pytest -q
```

Tests use temporary databases and fake or mocked analyzers, so they do not use your API key. They cover routing cases A–L, normalization, replay and concurrency, ordered dossier threads, triage, automatic attachment classification, pending agreements, invalid analyzer JSON, human approval/rejection, and action history.

## Send fixtures with curl

In a second terminal, from the repository root, activate the virtual environment:

```bash
source .venv/bin/activate

curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary @fixtures/A.json \
  http://127.0.0.1:8000/webhooks/whatsapp | python -m json.tool

curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary @fixtures/G.json \
  http://127.0.0.1:8000/webhooks/email | python -m json.tool

curl --fail --silent --show-error \
  http://127.0.0.1:8000/dossiers/D-1234/messages | python -m json.tool

curl --fail --silent --show-error \
  http://127.0.0.1:8000/triage | python -m json.tool
```

A new webhook returns HTTP `201` with `duplicate=false`; replaying the same fixture returns HTTP `200` with `duplicate=true` and the saved message. No second analysis or action is performed. Deduplication uses the external identifier within each channel.

## API

| Endpoint | Result |
| --- | --- |
| `POST /webhooks/whatsapp` | Normalize, route, save, and analyze attached WhatsApp input. |
| `POST /webhooks/email` | Normalize, route, save, and analyze attached email input. |
| `GET /dossiers/{reference}/messages` | Dossier details, messages newest first, action history, and document requests. |
| `GET /triage` | All unattached messages, including Sara's `needs_choice` message. |
| `POST /proposals/{id}/validate` | Apply a pending action and record the reviewer and source message. |
| `POST /proposals/{id}/reject` | Reject a pending proposal without applying its action. |

Both review endpoints require a JSON body such as `{"reviewer":"tutor-1"}`. Proposal IDs appear in `message.proposals` in webhook responses and dossier threads. A review returns `404` for an unknown proposal and `409` for an already reviewed proposal. Invalid webhook input or reviewer identifiers return `422`; an unknown dossier returns `404`.

## Demonstration and implementation

Follow [the 20-minute demo guide](docs/demo.md) to start with a fresh seeded database, replay A–L, show threads and triage, demonstrate duplicate prevention, classify an attachment, and approve an agreement proposal. It includes the commands and expected results.

The implementation separates payload normalization, pure routing, persistence, and analysis. Contacts belong to people, and dossier membership goes through participants. A phone number can therefore identify multiple dossiers and a dossier can have multiple participants. WhatsApp routing preserves the foreign-reference guard; email routing checks recipient address, outgoing thread, then subject reference. See [implementation decisions](docs/decisions.md).

Migrations are in `courtee/migrations/`; seed setup is in `courtee/db.py`; routing fixtures are in `fixtures/`; analysis fixtures are in `fixtures/analysis/`; automated checks are in `tests/`. WhatsApp/email delivery and attachment contents are simulated; dossier choices are logged as an interactive message. The reviewer identifier is supplied by the exercise client.

## Production next steps

1. Verify webhook authenticity and authenticate staff review endpoints.
2. Move analysis to background jobs with retries and replay-safe action processing.
3. Add database backups and tune indexes for measured query load.
4. Monitor routing failures, manual review, provider errors, and latency.
5. Enforce retention and access controls, and store attachments securely.
