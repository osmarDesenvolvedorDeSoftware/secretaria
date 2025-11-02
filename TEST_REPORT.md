# Secretária OsmarDev Local Test Report

## 1. Environment Setup

| Step | Status | Notes |
| --- | --- | --- |
| System packages (python3, redis, postgresql, etc.) | ✅ | Installed via `apt-get` and services enabled (`service redis-server start`, `service postgresql start`). |
| Python virtualenv & dependencies | ✅ | Created `venv`, upgraded `pip`, and installed `requirements.txt`. |
| Environment variables | ⚠️ | `.env` configured per instructions but `SHARED_SECRET` required a local value (`local-test-secret`) to satisfy HMAC validation. |
| Database migrations | ✅ | Ran `PYTHONPATH=/workspace/secretaria alembic upgrade head`; adjusted `companies_id_seq` and inserted local tenant (`127.0.0.1:5005`). |
| Redis availability | ✅ | Verified via `redis-server` service and queue inspection. |

## 2. Functional Flow Validation

| Flow Element | Status | Evidence |
| --- | --- | --- |
| Webhook ingestion | ✅ | `/webhook/whaticket` returned `202` with HMAC headers; payload logged with normalized number/text. See [`docs/local_test_logs.md`](docs/local_test_logs.md). |
| Queueing in RQ | ✅ | Job enqueued in `default:company_3`; inspected via Redis (`queue.count == 1`). |
| LLM response (Gemini) | ❌ | `process_incoming_message` retries failed with `Gemini returned status 401` because the service authenticates using an `Authorization: Bearer` header. Direct curl using `x-goog-api-key` succeeded. |
| WhatsApp/Whaticket delivery | ✅ | Despite LLM failure, fallback transfer message was sent; delivery recorded with status `SENT`. See [`docs/local_test_logs.md`](docs/local_test_logs.md) and `delivery_logs` table. |
| Logging | ✅ | Payloads and events captured in `/var/log/secretaria/app.log` (excerpted in `docs/local_test_logs.md`). |

## 3. Gemini API Direct Test

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{
    "contents": [
      {
        "parts": [
          {"text": "Explain how AI works in a few words"}
        ]
      }
    ]
  }'
```

Response contained a valid candidate text, confirming the API key works with the expected header.

## 4. Automated Tests

- Command: `pytest -v tests/`
- Result: 112 passed, 599 warnings

## 5. Metrics

- Mean Gemini latency during failure attempts: **0.240 s** (three retries, all 401).
- Delivery log entry (id=1) stored fallback message for number `5516996246673` with status `SENT`.

## 6. Final Verdict

- Installation & infrastructure: ✅
- Webhook + queue: ✅
- Gemini integration: ❌ (requires header change to `x-goog-api-key` for successful authentication)
- Whaticket delivery fallback: ✅

**Overall result: ❌ Falhou (Gemini authentication in application still invalid).**
