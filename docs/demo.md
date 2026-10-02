Run the commands from the repository root. Stop any server already using port 8000.

## 1. Explain the project

“This backend centralizes WhatsApp messages and emails into mortgage dossiers. It normalizes the input, applies routing rules, and saves each message once. Attached messages enter analysis. Low-risk actions can apply automatically; other actions need human approval. Every applied action leaves history.”

- Python and FastAPI expose and validate the API.
- SQLite stores relationships, messages, proposals, and history.
- Routing is a pure function, tested independently.
- Fake and OpenRouter analyzers use the same validated output contract.

## 2. Start a fresh database

In terminal 1:

```bash
source .venv/bin/activate
export COURTEE_DB="$(mktemp -d)/courtee.sqlite3"
python -m courtee.cli init-db
COURTEE_ANALYZER=fake python -m uvicorn courtee.api:app --host 127.0.0.1 --port 8000
```

“The database has the required seed data. The fake analyzer makes the demo repeatable.”

Open http://127.0.0.1:8000/docs to show the endpoints.

## 3. Run routing cases A–L

In terminal 2:

```bash
source .venv/bin/activate
set -o pipefail

for demo_case in A B C D E F G H I J K L; do
  case "$demo_case" in
    G|H|I|J) demo_channel=email ;;
    *) demo_channel=whatsapp ;;
  esac
  printf 'Case %s: ' "$demo_case"
  curl -fsS -H 'Content-Type: application/json' \
    --data-binary "@fixtures/$demo_case.json" \
    "http://127.0.0.1:8000/webhooks/$demo_channel" |
    python -c 'import json,sys; r=json.load(sys.stdin); m=r["message"]; print(m["dossier_reference"], m["routing_method"], m["routing_state"], "duplicate=" + str(r["duplicate"]))'
done
```

| Case | Explain |
| --- | --- |
| A | Youssef has one active dossier. |
| B | Sara's reply context identifies D-2041. |
| C | Sara has two dossiers; her message stays `needs_choice`. |
| E | A reference to someone else's dossier goes to triage. |
| G–I | Email routing uses the recipient, thread, then subject. |
| L | A local phone number is normalized correctly. |

Show the case C log in terminal 1: it lists Sara's two dossiers.

## 4. Show saved messages and replay

```bash
curl -fsS http://127.0.0.1:8000/dossiers/D-1234/messages | python -m json.tool
curl -fsS http://127.0.0.1:8000/dossiers/D-2041/messages | python -m json.tool
curl -fsS http://127.0.0.1:8000/triage | python -m json.tool
```

Expect 6 messages in D-1234, 3 in D-2041, and 4 triage entries, including Sara. Threads are newest first.

Replay K:

```bash
curl -fsS -H 'Content-Type: application/json' \
  --data-binary @fixtures/K.json \
  http://127.0.0.1:8000/webhooks/whatsapp | python -m json.tool

curl -fsS http://127.0.0.1:8000/dossiers/D-1234/messages | python -m json.tool
```

Point out `duplicate=true` and the unchanged message count.

“The database enforces uniqueness within each channel. A replay returns the saved message without repeating analysis or actions.”

## 5. Show automatic action and human approval

Send the simulated salary certificate:

```bash
curl -fsS -H 'Content-Type: application/json' \
  --data-binary @fixtures/analysis/attachment.json \
  http://127.0.0.1:8000/webhooks/whatsapp | python -m json.tool
```

Show `attestation_salaire` in `attachment_classifications`. The low-risk action records history with `applied_by=auto`.

Send the agreement and capture its proposal ID:

```bash
demo_agreement=$(curl -fsS -H 'Content-Type: application/json' \
  --data-binary @fixtures/analysis/agreement.json \
  http://127.0.0.1:8000/webhooks/email)
printf '%s\n' "$demo_agreement" | python -m json.tool
demo_proposal_id=$(printf '%s\n' "$demo_agreement" |
  python -c 'import json,sys; print(json.load(sys.stdin)["message"]["proposals"][0]["id"])')

curl -fsS http://127.0.0.1:8000/dossiers/D-1234/messages | python -m json.tool
```

The proposal is `pending`; the dossier remains `submitted`. Medium-risk actions require approval, regardless of confidence.

Validate it:

```bash
curl -fsS -H 'Content-Type: application/json' \
  --data-binary '{"reviewer":"tutor-1"}' \
  "http://127.0.0.1:8000/proposals/$demo_proposal_id/validate" | python -m json.tool

curl -fsS http://127.0.0.1:8000/dossiers/D-1234/messages | python -m json.tool
```

Show `accord_principe`, the applied proposal, and history linking the status change to its reviewer and source message.

## 6. Finish with tests

```bash
python -m pytest -q
python -m pytest tests/test_analysis.py -v \
  -k 'invalid_fake_analyzer_json or rejection_records'
```

The last full run passed 192 tests. Explain that invalid analyzer output causes manual review without applying actions; rejection leaves the dossier unchanged. Tests also cover concurrent replays and rollback.

Finish with the README's five production next steps.

Use a new `id` or `Message-ID` when changing a payload. For live OpenRouter, restart with `COURTEE_ANALYZER=llm` and send a new message. Show `analysis.status=completed`, the extracted entities, and `reply_draft`.

For the API walkthrough in Postman, see [postman.md](postman.md).
