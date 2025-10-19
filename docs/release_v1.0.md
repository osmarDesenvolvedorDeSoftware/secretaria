# Release Notes – Secretaria Virtual v1.0

## Visão Geral do Sistema

A Secretaria Virtual v1.0 consolida a plataforma multicanal para atendimento automatizado no Whaticket com os seguintes componentes principais:

- **API Flask (`run.py`, `app/`)** – expõe o webhook seguro `/webhook/whaticket`, autenticação do painel e endpoints internos de monitoramento (`/healthz`, `/metrics`).
- **Motor de Contexto (`app/services/context_engine.py`)** – mantém histórico, perfis e personalização com Redis e PostgreSQL.
- **Workers RQ (`app/services/tasks.py`, `rq` workers)** – processam mensagens em fila assíncrona, chamando modelos LLM e Whaticket.
- **Persistência** – PostgreSQL (conversas, logs, configurações) e Redis (contexto volátil, rate limiting, sessões JWT).
- **Observabilidade** – métricas Prometheus, logs estruturados (structlog) e alertas configuráveis.
- **Segurança** – HMAC de webhook, políticas de retry com circuit breaker, sanitização de logs e validação de autenticação JWT para painel.

Fluxo principal:
1. Whaticket envia evento HTTP assinado para `/webhook/whaticket`.
2. API valida assinatura HMAC, saneia payload e grava contexto.
3. Mensagens são enfileiradas no Redis (RQ) e consumidas pelo worker.
4. Worker enriquece contexto via LLM, aplica regras de empatia/personalização e responde no Whaticket.
5. Métricas e logs são publicados para Prometheus/Grafana.

## Instruções de Deploy (`scripts/deploy.sh`)

1. Garanta variáveis `.env` atualizadas (`DATABASE_URL`, `REDIS_URL`, tokens Whaticket e LLM, `MONITORING_WEBHOOK_URL`).
2. Execute `scripts/deploy.sh [major|minor|patch]` de acordo com o tipo de versão:
   ```bash
   ./scripts/deploy.sh minor
   ```
3. O script cria a nova tag semântica, constrói a imagem da aplicação, atualiza dependências externas, sobe o stack com `docker-compose.prod.yml` e aplica migrações Alembic.
4. Caso `MONITORING_WEBHOOK_URL` esteja definido, o script notifica automaticamente o canal de monitoramento.
5. Verifique `docker compose -f docker-compose.prod.yml ps` e o healthcheck `/healthz` para validar o deploy.

### Rollback

- Identifique a tag anterior com `git describe --tags --abbrev=0 --exclude $(git describe --tags --abbrev=0)` ou consulte o histórico de releases.
- Pare a stack atual: `docker compose -f docker-compose.prod.yml down`.
- Faça checkout da tag anterior: `git checkout vX.Y.Z`.
- Reaplique o deploy: `./scripts/deploy.sh patch` (recria a mesma tag localmente) ou `docker compose -f docker-compose.prod.yml up -d` para reativar serviços com a imagem anterior.
- Execute `docker compose -f docker-compose.prod.yml run --rm app alembic downgrade -1` se for necessário desfazer a última migração.

## Métricas e Dashboards

- Endpoints Prometheus: `GET /metrics` (API) e exporters padrão dos serviços Docker.
- Métricas chave:
  - `secretaria_webhook_received_total{status}` – aceitação/rejeição de eventos.
  - `secretaria_task_latency_seconds` – latência do pipeline de atendimento.
  - `secretaria_llm_latency_seconds`, `secretaria_llm_errors_total` – saúde do provedor de IA.
  - `secretaria_whaticket_send_success_total`, `secretaria_whaticket_errors_total`, `secretaria_whaticket_send_retry_total` – entrega e retries no Whaticket.
  - `secretaria_queue_size` – tamanho da fila principal.
  - `secretaria_sentiment_average_gauge`, `secretaria_satisfaction_ratio_gauge`, `secretaria_intention_distribution_total` – telemetria de IA.
  - `secretaria_healthcheck_failures_total{component}` – disponibilidade de Redis, PostgreSQL e workers.
- Dashboards recomendados:
  - **Grafana – Atendimento em Tempo Real**: latência por etapa, tamanho da fila, taxa de retries Whaticket.
  - **Grafana – Qualidade de IA**: tendências de sentimento/intenção, satisfação média.
  - **Grafana – Infraestrutura**: uso de CPU/RAM dos containers, métricas Redis e PostgreSQL (via exporters).
- Alertas:
  - Latência média > 1s no webhook ou fila > 50 itens por mais de 5 minutos.
  - `secretaria_healthcheck_failures_total` > 0 em janelas de 1 minuto.
  - `secretaria_whaticket_errors_total` incrementando continuamente.

## Requisitos Mínimos de Hardware

| Ambiente | CPU | RAM | Storage | Observações |
| --- | --- | --- | --- | --- |
| Produção (volume médio) | 4 vCPUs | 8 GiB | 40 GiB SSD | Permite 3 workers RQ, PostgreSQL e Redis na mesma VM com margem para picos. |
| Staging | 2 vCPUs | 4 GiB | 20 GiB SSD | Stack reduzida sem cargas reais. |
| Desenvolvimento local | 2 vCPUs | 4 GiB | 15 GiB SSD | Docker Desktop ou podman. |

## Atualização de Versão (CI/CD)

1. A pipeline CI executa `pytest`, lint (`ruff`), build Docker e verificação de migrações.
2. Após merge em `main`, a pipeline dispara `scripts/deploy.sh patch` em staging.
3. Aprovação manual promove a mesma tag para produção.
4. Para upgrades major/minor, crie branch de release, ajuste changelog e execute `./scripts/deploy.sh minor|major`.
5. Certifique-se de alinhar versão do `.env` e dashboards ao novo tag (atualizar variáveis e painéis se métricas novas forem adicionadas).

## Validações de Segurança e Conformidade

- **HTTP**: TLS terminação via reverse proxy, verificação de timestamps (±300s) e assinatura HMAC bloqueiam replays.
- **JWT**: painel exige tokens curtos com refresh em Redis e revogação automática em logout/expiração.
- **Docker**: `docker-compose.prod.yml` usa rede interna isolada; apenas API e Prometheus ficam expostos, demais serviços permanecem privados.
- **Sanitização**: logs mascaram dados sensíveis (`tests/test_logs_masking.py`) e payloads passam por filtros de prompt-injection.
- **Dependências**: `requirements.txt` atualizado, sem libs obsoletas conhecidas; lint `ruff` garante ausência de imports/variáveis redundantes.

Com todos os testes (`pytest`, métricas, painel, contexto) aprovados e validações de segurança revisadas, a Secretaria Virtual está pronta para a release **v1.0**.
