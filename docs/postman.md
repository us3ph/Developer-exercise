# Postman demo

Start the backend using the startup block in [demo.md](demo.md), with `COURTEE_ANALYZER=fake`.

## Import and run

In the Postman desktop app, import:

- [API collection](../postman/Courtee.postman_collection.json)
- [OpenRouter collection](../postman/Courtee-OpenRouter.postman_collection.json)
- [Local environment](../postman/Courtee-local.postman_environment.json)

Select **Courtee local**. Its `base_url` is `http://127.0.0.1:8000`; change it if your server uses another port.

Select **Courtee API → Run**, keep all folders in order, and start one iteration. The [Collection Runner](https://learning.postman.com/docs/tests-and-scripts/running-collections/intro-to-collection-runs/) executes the requests and checks their responses automatically.

| Folder | What to show |
| --- | --- |
| Setup | Existing dossier and triage counts. |
| Routing A–L | Correct dossier, method, state, and normalized phone numbers. |
| Threads and replays | Saved messages, newest-first order, triage, and unchanged replay counts. |
| Analysis and review | Automatic classification/document request, pending agreement, approval, rejection, and history. |
| Errors | Invalid input (`422`), missing records (`404`), and repeated review (`409`). |

Open a request in the run results to show its response and passing tests. Each run uses new message IDs and checks changes against the existing data, so you can run it again.

## Show real OpenRouter analysis

Stop the server, then restart it in the same terminal:

```bash
COURTEE_ANALYZER=llm python -m uvicorn courtee.api:app --host 127.0.0.1 --port 8000
```

Your `OPENROUTER_API_KEY` stays in the backend's `.env`.

Run **Courtee OpenRouter** in order. It sends one new agreement to D-2041, checks extracted entities and `reply_draft`, approves the pending action, and verifies saved history and replay behavior.

Show `analysis.status=completed` and the server's OpenRouter request log. If live analysis fails, the collection reports a failed check and stops before approval.

Malformed analyzer output, concurrent delivery, and rollback are covered by the automated Python tests.
