# Secretaria Virtual Whaticket

Arquitetura pronta para produção para uma secretária virtual integrada ao Whaticket com Flask, Redis, RQ e PostgreSQL.

## Visão Geral

* **Webhook seguro** com validação HMAC (`X-Signature`) e token opcional (`X-Webhook-Token`).
* **Persistência** de conversas e logs de entrega em PostgreSQL (SQLAlchemy + Alembic).
* **Memória de curto prazo** e **rate limiting** via Redis.
* **Fila assíncrona** com RQ para chamadas ao LLM e envio ao Whaticket.
* **Integração Whaticket** com token estático ou login JWT opcional com cache em Redis.
* **Cliente Gemini** com retries, timeout e circuit breaker.
* **Observabilidade** com logs estruturados (structlog) e métricas Prometheus em `/metrics`.
* **Segurança** com sanitização, proteção contra prompt-injection e CORS desabilitado no webhook.
* **Testes** com pytest + coverage e ambiente Docker pronto.

## Requisitos

* Python 3.11
* Redis
* PostgreSQL 14+

## Configuração

1. Copie o arquivo `.env.example` para `.env` e ajuste as variáveis.
2. Execute `make up` para subir o stack (web + worker + redis + postgres).
3. Rode as migrações com `make upgrade`.
4. Opcional: `make dev` para desenvolvimento local com auto-reload.

## Comandos Principais

```bash
make up          # inicia docker-compose
make down        # derruba os containers
make migrate     # cria nova migração Alembic
make upgrade     # aplica migrações
make worker      # inicia o worker RQ
make test        # executa pytest com cobertura
make dev         # roda Flask com debug
```

## Webhook Whaticket

* URL: `POST /webhook/whaticket`
* Headers obrigatórios:
  * `X-Timestamp`: epoch UNIX (segundos) gerado no momento do envio
  * `X-Signature`: `hex(hmac_sha256(SHARED_SECRET, f"{timestamp}.{raw_body}"))`
  * `Content-Type: application/json`
  * `X-Webhook-Token`: opcional, se configurado
* Resposta: `202 Accepted` com `{ "queued": true }` quando a mensagem é enfileirada.

### Gerando a assinatura HMAC

```python
import hmac
from hashlib import sha256

secret = "minha-shared-secret"
timestamp = 1700000000
raw_body = b'{"message": {"conversation": "olá"}, "number": "5511999999999"}'

message = f"{timestamp}.".encode() + raw_body
signature = hmac.new(secret.encode(), message, sha256).hexdigest()

headers = {
    "X-Timestamp": str(timestamp),
    "X-Signature": signature,
}
```

Envie o payload em até ±300 segundos do `X-Timestamp` informado para evitar rejeição por replay.

### Exemplos de payloads

* **Mensagem de texto**

```json
{
  "message": {"conversation": "olá"},
  "number": "5511999999999"
}
```

* **Interativo (botão/lista)**

```json
{
  "message": {
    "buttonsResponseMessage": {
      "selectedDisplayText": "Quero falar com suporte"
    }
  },
  "ticket": {"contact": {"number": "5511988877766"}}
}
```

* **Payload inválido (faltando número)**

```json
{
  "message": {"conversation": "olá"}
}
```

## Métricas

Expostas em `/metrics` no formato Prometheus. Inclui contadores de webhooks aceitos/rejeitados, histogramas de latência (LLM, Whaticket, processamento) e gauge da fila RQ.

## Estrutura de Pastas

Veja a árvore completa no repositório para entender os módulos de rotas, serviços, modelos e workers.

## Observabilidade

* Logs em JSON com `correlation_id`, método, status e duração.
* Métricas Prometheus para integrar com Grafana/Alertmanager.

## Testes

```bash
pytest -v --maxfail=1 --disable-warnings
make up
curl -i http://localhost:8080/healthz
```

Os testes cobrem o parsing completo do payload, a proteção HMAC com `X-Timestamp` anti-replay, o cliente Whaticket com retries, sanitização de logs, bloqueio de prompt-injection e o fluxo end-to-end do webhook com enfileiramento no Redis.

## Docker

O diretório `docker/` inclui Dockerfile, docker-compose e configuração do RQ worker. O serviço web expõe a porta 8080.

## Limitações

* LLM Gemini precisa de chave válida e rede externa.
* Migrações devem ser executadas antes do primeiro uso em produção.
