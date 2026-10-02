# 20-minute demonstration

Run all commands from the repository root after installing the project with the [README](../README.md). Use two terminals with the virtual environment active. Stop any server already using port 8000 before starting this demo.

| Time | Demonstrate |
| --- | --- |
| 0–2 minutes | Fresh database, migrations, seed, and fake analyzer. |
| 2–8 minutes | Routing cases A–L and the server's simulated dossier-choice log. |
| 8–11 minutes | D-1234 and D-2041 threads, plus Sara and ordinary triage. |
| 11–13 minutes | Case K replay without another saved message or analysis. |
| 13–17 minutes | Automatic attachment classification and a human-approved agreement. |
| 17–20 minutes | Automated tests, setup commands, and five production next steps. |

## 1. Start with a fresh database

In terminal 1:

```bash
source .venv/bin/activate
export COURTEE_DB="$(mktemp -d)/courtee.sqlite3"
python -m courtee.cli init-db
COURTEE_ANALYZER=fake python -m uvicorn courtee.api:app --host 127.0.0.1 --port 8000
```

This creates a separate temporary database for each rehearsal. It starts with three active dossiers at `submitted` and two outgoing messages. The explicit fake setting overrides local live configuration.

## 2. Replay the routing fixtures

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
  curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    --data-binary "@fixtures/$demo_case.json" \
    "http://127.0.0.1:8000/webhooks/$demo_channel" |
    python -c 'import json, sys; r=json.load(sys.stdin); m=r["message"]; print(m["dossier_reference"], m["routing_method"], m["routing_state"], "duplicate=" + str(r["duplicate"]))'
done
```

On a fresh database every response has `duplicate=False`:

| Case | Dossier | Method | State |
| --- | --- | --- | --- |
| A | D-1234 | `single_dossier` | `attached` |
| B | D-2041 | `context` | `attached` |
| C | None | `needs_choice` | `needs_choice` |
| D | D-2077 | `reference` | `attached` |
| E | None | `triage` | `triage` |
| F | None | `triage` | `triage` |
| G | D-1234 | `dossier_address` | `attached` |
| H | D-1234 | `thread` | `attached` |
| I | D-2041 | `subject_reference` | `attached` |
| J | None | `triage` | `triage` |
| K | D-1234 | `single_dossier` | `attached` |
| L | D-1234 | `single_dossier` | `attached` |

Show the case C log in terminal 1: the simulated interactive message lists D-2041 and D-2077 for Sara. Explain case E: Youssef's foreign dossier reference goes to triage before the single-dossier fallback. Case L recognizes his local phone format.

## 3. Show dossier threads and triage

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:8000/dossiers/D-1234/messages | python -m json.tool
curl --fail --silent --show-error \
  http://127.0.0.1:8000/dossiers/D-2041/messages | python -m json.tool
curl --fail --silent --show-error \
  http://127.0.0.1:8000/triage | python -m json.tool
```

D-1234 has six messages, D-2041 has three, and D-2077 has one, including the two seeds across the threads. Dossier messages are ordered by event time, newest first. Email fixtures without a timestamp use receipt time.

Triage has four entries: C (`needs_choice`, candidates D-2041/D-2077), E, F, and J (`triage`). There are 14 messages in total.

## 4. Replay case K

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary @fixtures/K.json \
  http://127.0.0.1:8000/webhooks/whatsapp | python -m json.tool
```

The response has `duplicate=true`. Its saved message and analysis are reused, and the D-1234 thread still has six messages. The server's case C choice log remains a single entry.

## 5. Show automatic classification and human validation

First, send a simulated salary certificate:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary @fixtures/analysis/attachment.json \
  http://127.0.0.1:8000/webhooks/whatsapp | python -m json.tool
```

The response contains `analysis.status=completed` and an `attestation_salaire` classification. Its low-risk action at confidence 0.95 has been applied with `applied_by=auto` in D-1234's history.

Next, save the agreement response and extract its pending proposal ID:

```bash
demo_agreement=$(curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary @fixtures/analysis/agreement.json \
  http://127.0.0.1:8000/webhooks/email)
printf '%s\n' "$demo_agreement" | python -m json.tool
demo_proposal_id=$(printf '%s\n' "$demo_agreement" |
  python -c 'import json, sys; m=json.load(sys.stdin)["message"]; p=[p for p in m["proposals"] if p["status"] == "pending" and p["action"]["type"] == "set_status"]; assert len(p) == 1; print(p[0]["id"])')

curl --fail --silent --show-error \
  http://127.0.0.1:8000/dossiers/D-1234/messages | python -m json.tool
```

The medium-risk `set_status` action remains pending, and D-1234 is still `submitted`. The extracted ID comes from the API; no fixed database ID is assumed.

Validate it as the tutor and fetch the resulting thread:

```bash
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary '{"reviewer":"tutor-1"}' \
  "http://127.0.0.1:8000/proposals/$demo_proposal_id/validate" | python -m json.tool

curl --fail --silent --show-error \
  http://127.0.0.1:8000/dossiers/D-1234/messages | python -m json.tool
```

The proposal is `applied`, the dossier is `accord_principe`, and history records `submitted → accord_principe`, `applied_by=tutor-1`, the proposal ID, and its source message ID. D-1234 now has eight messages and two history entries: automatic classification and human validation.

## 6. Finish with tests and reproducible setup

```bash
python -m pytest -q
```

Show the four required analysis scenarios in `tests/test_analysis.py`: automatic classification, pending agreement, malformed JSON/manual review, and human validation. The invalid-output case is tested through an injected fake response; no malformed inbound message is needed for the demo.

Open the README's setup and fixture commands, then its five production next steps. Stop the demo server in terminal 1 with `Ctrl+C`. For another rehearsal, start section 1 again to create another fresh database.
