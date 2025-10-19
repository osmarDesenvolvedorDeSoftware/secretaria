# Secretaria Virtual Whaticket

[![Release](https://img.shields.io/badge/version-v2.2-blue.svg)](docs/release_v2.2.md)

Arquitetura pronta para produção para uma secretária virtual integrada ao Whaticket com Flask, Redis, RQ e PostgreSQL.

## Visão Geral

- 📄 [Documentação de release v1.0](docs/release_v1.0.md)
- 📄 [Documentação de release v1.1](docs/release_v1.1.md)
- 📄 [Documentação de release v1.2](docs/release_v1.2.md)
- 📄 [Documentação de release v1.3](docs/release_v1.3.md)
- 📄 [Documentação de release v2.0](docs/release_v2.0.md)
- 📄 [Documentação de release v2.1](docs/release_v2.1.md)
- 📄 [Documentação de release v2.2](docs/release_v2.2.md)

* **Multi-tenancy completo** com isolamento por empresa em banco, Redis, filas RQ e JWT multiempresa.
* **Provisionamento automático** via `/api/tenants/provision` com criação de planos, assinaturas, schemas e redis dedicados.
* **Gestão de planos e billing** com modelos `Plan`/`Subscription`, uso em tempo real e webhooks configuráveis.
* **Webhook seguro** com validação HMAC (`X-Signature`) e token opcional (`X-Webhook-Token`).
* **Persistência** de conversas e logs de entrega em PostgreSQL (SQLAlchemy + Alembic).
* **Memória de curto prazo** e **rate limiting** via Redis com quotas por tenant.
* **Fila assíncrona** com RQ para chamadas ao LLM e envio ao Whaticket.
* **Integração Whaticket** com token estático ou login JWT opcional com cache em Redis.
* **Cliente Gemini** com retries, timeout e circuit breaker.
* **Observabilidade** com logs estruturados (structlog), métricas Prometheus e dashboards de consumo e custos por empresa.
* **Segurança** com sanitização, proteção contra prompt-injection e CORS desabilitado no webhook.
* **Testes** com pytest + cobertura e ambiente Docker pronto.
* **Agenda Inteligente** com integração Cal.com multi-tenant, webhook assinado e orquestração direta via WhatsApp.
* **Lembretes e reagendamento inteligente** com confirmações proativas, métricas Prometheus e painel com taxa de presença.

## Requisitos

* Python 3.11
* Redis
* PostgreSQL 14+

## Configuração

1. Copie o arquivo `.env.example` para `.env` e ajuste as variáveis.
2. Execute `make up` para subir o stack (web + worker + redis + postgres).
3. Rode as migrações com `make upgrade`.
4. Opcional: `make dev` para desenvolvimento local com auto-reload.

### Multiempresa e Billing

1. Crie ou ajuste planos em `/painel/empresas` e configure limites (`Plan`).
2. Registre empresas com domínio único e vincule um plano ativo.
3. Configure o webhook de pagamento no provedor (ex.: Stripe, Mercado Pago ou manual) apontando para `/webhook/billing` com o header `X-Company-Domain`.
4. Garanta que clientes externos enviem `X-Company-Domain` ou incluam `company_id` no JWT para roteamento correto.

### Analytics e Faturamento em Tempo Real

1. A nova aba **Analytics e Consumo** do painel consolida métricas diárias e semanais (mensagens, tokens, tempo médio de resposta e custo estimado) por empresa.
2. O backend agrega o uso em tempo real, calcula o custo incremental com base em `BILLING_COST_PER_MESSAGE` e `BILLING_COST_PER_THOUSAND_TOKENS` e dispara alertas quando 80% e 100% do plano são atingidos.
3. Utilize os endpoints protegidos `/api/analytics/summary?company_id=...` e `/api/analytics/history?period=week|month&company_id=...` para integrar com outras ferramentas.
4. Gere relatórios CSV ou PDF diretamente pelo painel ou via CLI com `make report COMPANY_ID=<id> FORMAT=csv|pdf`.

### v2.0 – IA de Negócios & Compliance

1. A nova aba **IA de Negócios** centraliza os insights de churn, recomendações de upsell e a “next best action” por tenant, além de gráficos de NPS e acompanhamento de testes A/B.
2. O motor de recomendações (`/api/recommendations/*`) combina RFM simplificado, consumo em tempo real e feedbacks coletados para sugerir upgrades e ações automáticas, registrando gatilhos no painel e webhooks por tenant.
3. O módulo de **A/B testing** (`/api/abtests/*`) cria experimentos epsilon-greedy, registra eventos de impressões/conversões e promove variantes vencedoras, com gerenciamento completo no painel.
4. A seção de **Compliance LGPD** oferece exportação e exclusão auditada por telefone, políticas de retenção configuráveis e auditoria centralizada (`AuditLog`).
5. Novos SLOs e alertas expõem métricas de latência de webhook, taxa de sucesso Whaticket e erro do LLM, com gauge de error budget e scripts de DR (`make backup`/`make restore`).

### Provisionamento automático de tenants

1. Autentique-se no painel (`/painel`) e utilize a seção **Nova Empresa** para informar nome, domínio, slug e plano.
2. O backend criará registros de `Plan`, `Company` e `Subscription`, um schema PostgreSQL (`tenant_<id>`), Redis isolado e fila RQ.
3. O painel exibirá o progresso do provisionamento e fornecerá o token inicial de acesso e o comando `python scripts/spawn_worker.py --company-id <id>`.
4. Após o deploy, execute `scripts/deploy.sh --tenant-id <id> --domain exemplo.com` para registrar subdomínios `chat.<tenant>.exemplo.com` e `api.<tenant>.exemplo.com` com status de SSL.
5. Inicie o worker dedicado com `python scripts/spawn_worker.py --company-id <id>` (opcionalmente definindo `--queue` ou `--burst`).

### v2.1 – Agenda Inteligente (Cal.com)

1. Preencha os campos `cal_api_key`, `cal_default_user_id` e `cal_webhook_secret` da empresa no painel ou via banco de dados.
2. Configure o webhook do Cal.com para `POST /api/agenda/webhook/cal` com os headers `X-Cal-Company` e `X-Cal-Signature` (HMAC SHA-256).
3. Ative o fluxo WhatsApp: mensagens do cliente pedindo agendamento retornam sugestões automáticas de horário e confirmação com link.
4. Utilize a nova aba **Agenda** do painel para visualizar compromissos, filtrar por data/cliente e criar reuniões manualmente.
5. Monitore as métricas `secretaria_appointments_*` no Grafana “Agenda Inteligente” para acompanhar taxa de confirmação e latência.

### v2.2 – Lembretes e Reagendamento Inteligente

1. Lembretes automáticos 24h e 1h antes do início com botões de confirmação e opção de reagendar direto pelo WhatsApp.
2. Fluxo de reagendamento inteligente reutilizando a disponibilidade Cal.com, atualizando status antigos e registrando auditoria e métricas.
3. Detecção de no-show com feedback automático, taxa de presença no painel, filtros rápidos e ação “Enviar lembrete agora”.

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

## Healthcheck Profundo

O endpoint `GET /healthz` só retorna `200 OK` quando **todas** as dependências respondem dentro da janela esperada:

* PostgreSQL: `SELECT 1` via SQLAlchemy.
* Redis: comando `PING`.
* Worker RQ: presença de heartbeat válido (`rq:workers` + `last_heartbeat`).

O JSON de resposta inclui a latência média de cada chamada (`latency_ms`), quantidade de workers visíveis e o timestamp em UTC. Em caso de falha, o status HTTP é `503` e a métrica `secretaria_healthcheck_failures_total{component="..."}` é incrementada.

## Métricas

Expostas em `/metrics` no formato Prometheus com `HELP`/`TYPE` padrão. Destaques:

* `secretaria_webhook_received_total{status="accepted|rejected"}`
* `secretaria_task_latency_seconds`
* `secretaria_whaticket_latency_seconds`
* `secretaria_whaticket_errors_total`
* `secretaria_whaticket_send_success_total`
* `secretaria_whaticket_send_retry_total`
* `secretaria_whaticket_delivery_success_ratio`
* `secretaria_llm_latency_seconds`
* `secretaria_llm_errors_total`
* `secretaria_llm_error_rate`
* `secretaria_llm_prompt_injection_blocked_total`
* `secretaria_appointments_total`
* `secretaria_appointments_confirmed_total`
* `secretaria_appointments_cancelled_total`
* `secretaria_appointments_latency_seconds`
* `secretaria_appointment_reminders_sent_total{type="24h|1h|manual"}`
* `secretaria_appointment_confirmations_total`
* `secretaria_appointment_reschedules_total`
* `secretaria_appointment_no_show_total`
* `secretaria_healthcheck_failures_total{component="redis|postgres|rq_worker"}`
* `secretaria_queue_size{company_id="..."}`
* `secretaria_usage_messages_total{company_id="..."}`
* `secretaria_usage_tokens_total{company_id="..."}`

### Exemplos `curl`

```bash
curl -s http://localhost:8080/metrics | grep secretaria_whaticket_send
curl -s http://localhost:8080/metrics | grep secretaria_healthcheck_failures_total
```

## Estrutura de Pastas

Veja a árvore completa no repositório para entender os módulos de rotas, serviços, modelos e workers.

## Observabilidade

* Logs em JSON com `correlation_id`, método, status, `company_id` e duração.
* Métricas Prometheus para integrar com Grafana/Alertmanager com labels por empresa e alertas por tenant.

## Recursos Inteligentes

* **Análise de Sentimento** – detecta polaridade e ajusta tom de voz automaticamente.
* **Classificação de Intenção** – identifica propósito da mensagem (suporte, vendas, follow-up) e atualiza métricas agregadas.
* **Empatia Adaptativa** – regula formalidade, empatia e humor conforme perfil e humor detectado.
* **Personalização Contextual** – resgata histórico, preferências e tópicos frequentes para respostas sob medida.

## Operação em Produção

### Workers RQ sob supervisão

Use um supervisor de processos para manter múltiplos workers ativos. Exemplos:

```bash
# Supervisor (arquivo pronto em supervisord.conf)
supervisord -c supervisord.conf

# PM2 executando 3 workers Python
pm2 start "python -m rq worker default" --name secretaria-worker --instances 3 --interpreter python3
```

### Monitorar fila padrão e dead-letter

```bash
rq info --url ${REDIS_URL} default dead_letter
rq info --url ${REDIS_URL} dead_letter --interval 5  # modo watch para DLQ
```

### Rotação de segredos

```bash
make rotate-secrets  # atualiza tokens do painel, Whaticket e LLM
```

O script grava novas credenciais em `.env` e no Redis, preservando backups com sufixo `*.bak`.

### Métricas Prometheus em tempo real

```bash
curl -s http://localhost:8080/metrics | grep secretaria_
watch -n 5 'curl -s http://localhost:8080/metrics | grep "queue_size\|dead_letter"'
```

Integre o endpoint `/metrics` com Prometheus/Grafana para alarmes de latência (`secretaria_task_latency_seconds`), retries
(`secretaria_whaticket_send_retry_total`) e tamanho da DLQ (`secretaria_dead_letter_queue_size`).

## Testes

```bash
pytest -v --maxfail=1 --disable-warnings --cov=app --cov-report=term-missing
make up
curl -s http://localhost:8080/healthz | jq
```

Os testes cobrem o parsing completo do payload, a proteção HMAC com `X-Timestamp` anti-replay, o cliente Whaticket com retries, sanitização de logs, bloqueio de prompt-injection, métricas/healthcheck e o fluxo end-to-end do webhook com enfileiramento no Redis.

## Política de Retentativas

* **Fila RQ:** cada mensagem é executada até 1 tentativa inicial + 5 re-tentativas automáticas.
* **Backoff progressivo:** 5s → 15s → 45s → 90s → 90s (padrão do RQ quando a lista termina).
* **Registro:** a métrica `secretaria_whaticket_send_retry_total` aumenta a cada re-tentativa.
* **Persistência:** falhas com retries disponíveis são registradas como `FAILED_TEMPORARY`; quando os limites se esgotam ou a falha não é re-tentável, o log é `FAILED_PERMANENT`.

## Validação em Staging

1. `docker compose -f docker/docker-compose.yml up -d`
2. `alembic upgrade head`
3. Executar `pytest -v --maxfail=1 --disable-warnings --cov=app --cov-report=term-missing`
4. Validar `/healthz` e `/metrics` (`curl -s http://localhost:8080/healthz | jq`)
5. Conferir métricas chave no Prometheus/Grafana e alarmes ativos
6. Enviar payload de teste pelo webhook para validar fila + entrega real

## Docker

O diretório `docker/` inclui Dockerfile, docker-compose e configuração do RQ worker. O serviço web expõe a porta 8080.

## Limitações

* LLM Gemini precisa de chave válida e rede externa.
* Migrações devem ser executadas antes do primeiro uso em produção.
