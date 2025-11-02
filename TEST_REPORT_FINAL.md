# Test Report Final

## Ambiente Local
- Ambiente Python isolado via `venv` com dependências instaladas (`pip install -r requirements.txt`).
- Serviços Redis e PostgreSQL em execução com usuário/banco `secretaria_user`/`secretaria` configurados.
- Migrações aplicadas com `PYTHONPATH=. alembic upgrade head`.

## Validação da API Gemini
```
$ curl https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{"contents":[{"parts":[{"text":"Explain how AI works in a few words"}]}]}'
{
  "candidates": [
    {
      "content": {
        "parts": [
          {
            "text": "AI analyzes vast data for patterns, learns from them, and then makes predictions or decisions."
          }
        ],
        "role": "model"
      },
      "finishReason": "STOP",
      "index": 0
    }
  ],
  "usageMetadata": {
    "promptTokenCount": 8,
    "candidatesTokenCount": 18,
    "totalTokenCount": 833,
    "promptTokensDetails": [
      {
        "modality": "TEXT",
        "tokenCount": 8
      }
    ],
    "thoughtsTokenCount": 807
  },
  "modelVersion": "gemini-2.5-flash",
  "responseId": "c7kHacCBDqiV_uMP66K62AM"
}
```

## Fluxo Whaticket → IA → Whaticket
- Webhook recebido (`202`), normalizado e enfileirado para a empresa 3. Trechos relevantes do log estruturado:
```
{"remote_addr": "127.0.0.1", "headers": {"Host": "127.0.0.1:5005", "User-Agent": "python-requests/2.31.0", "Accept-Encoding": "gzip, deflate", "Accept": "*/*", "Connection": "keep-alive", "Content-Type": "application/json", "X-Timestamp": "1762114220", "X-Signature": "87089f772becd34bcd2d5d7dc758ffb0f35e3c09d1cf6b08bc73435cf61b20f0", "X-Webhook-Token": "testtoken", "Content-Length": "170"}, "raw_body": "{\"key\":{\"remoteJid\":\"5516996246673@s.whatsapp.net\",\"fromMe\":false,\"id\":\"TESTE003\"},\"message\":{\"conversation\":\"Voc\\u00eas atendem aos fins de semana?\"},\"pushName\":\"Osmar\"}", "event": "webhook_payload_received", "company_id": 3, "correlation_id": "d3f43b85-b42a-4c5f-990a-dd1a3f9296cb", "timestamp": "2025-11-02T20:10:20.485123Z", "level": "info"}
{"number": "5516996246673", "text": "Voc\u00eas atendem aos fins de semana?", "kind": "text", "payload_format": "proto", "raw_payload": {"key": {"remoteJid": "5516996246673@s.whatsapp.net", "fromMe": false, "id": "TESTE003"}, "message": {"conversation": "Voc\u00eas atendem aos fins de semana?"}, "pushName": "Osmar"}, "event": "webhook_payload_parsed", "company_id": 3, "correlation_id": "d3f43b85-b42a-4c5f-990a-dd1a3f9296cb", "timestamp": "2025-11-02T20:10:20.486949Z", "level": "info"}
{"path": "/webhook/whaticket", "status": 202, "method": "POST", "duration": 0.011676788330078125, "event": "request_completed", "company_id": 3, "correlation_id": "d3f43b85-b42a-4c5f-990a-dd1a3f9296cb", "timestamp": "2025-11-02T20:10:20.494343Z", "level": "info"}
```
- Processamento da fila com worker em contexto Flask:
```
{"duration": 5.807896375656128, "company": "3", "event": "llm_call", "correlation_id": "73df912f-65f7-4c32-a6ad-0c965081b1b0", "timestamp": "2025-11-02T20:10:32.926602Z", "level": "info"}
{"task": "process_incoming_message", "company_id": 3, "number": "5516996246673", "kind": "text", "job_id": "1ed37ecc-91ab-443e-be85-d39b9d5ba64d", "attempt": 1, "retries_left": 5, "status": "success", "has_response": true, "response_chars": 248, "event": "llm_response_status", "correlation_id": "73df912f-65f7-4c32-a6ad-0c965081b1b0", "timestamp": "2025-11-02T20:10:32.931186Z", "level": "info"}
{"service": "whaticket", "company": "3", "number": "5516996246673", "has_id": false, "event": "whaticket_text_sent", "correlation_id": "73df912f-65f7-4c32-a6ad-0c965081b1b0", "timestamp": "2025-11-02T20:10:36.212172Z", "level": "info"}
{"task": "process_incoming_message", "company_id": 3, "number": "5516996246673", "kind": "text", "job_id": "1ed37ecc-91ab-443e-be85-d39b9d5ba64d", "attempt": 1, "retries_left": 5, "status": "SENT", "external_id": null, "success": true, "event": "whatsapp_send_status", "correlation_id": "73df912f-65f7-4c32-a6ad-0c965081b1b0", "timestamp": "2025-11-02T20:10:36.216474Z", "level": "info"}
```
- Fila após processamento: `Pending jobs: 0` / `Job IDs: []`.

## Métricas do LLM Gemini
- Chamadas registradas: 1 para a empresa 3 durante o fluxo real.
- Tempo médio de resposta calculado a partir do log acima: **5.81 s**.

## Testes Automatizados
```
112 passed, 599 warnings in 36.99s
```

