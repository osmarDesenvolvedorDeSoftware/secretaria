# Release v2.0 – IA de Negócios & Compliance

## Destaques

- 🧠 **Motor de recomendações por tenant** com análise de churn (RFM simplificado), uso de mensagens/tokens e feedbacks coletados via WhatsApp.
- 📈 **A/B testing nativo** com algoritmo epsilon-greedy, registro de eventos (impressões, cliques, respostas, conversões) e promoção automática da variante vencedora.
- 👍 **Feedback operacional** com quick-replies (👍/👎) e NPS simplificado integrados ao Whaticket, ajustando automaticamente tom e estratégias no `ContextEngine`.
- 🛡️ **Compliance LGPD** com exportação/remoção auditada, políticas de retenção configuráveis, masking de PII em logs/exports e `AuditLog` por ação sensível.
- 📊 **SLOs e error budget** monitorando latência de webhook, sucesso de entrega Whaticket e taxa de erro LLM, com gauges dedicados.
- 💾 **Backup & DR** simplificados via `scripts/backup.sh` e `scripts/restore.sh` (alvo dos comandos `make backup` e `make restore`).
- 🖥️ **Painel “IA de Negócios”** com insights de churn, recomendações, gráfico de NPS, gestão de testes A/B e console de compliance.

## Endpoints e Serviços

| Área | Endpoint/Serviço | Descrição |
| ---- | ---------------- | --------- |
| Recomendações | `POST /api/recommendations/evaluate` | Recalcula insights por empresa, dispara gatilhos (`billing/usage_near_limit`, `churn_risk`, `campaign_suggestion`) e opcionalmente configura webhook por tenant. |
| Recomendações | `GET /api/recommendations/insights` | Recupera o último insight armazenado em cache (Redis) por tenant. |
| A/B Testing | `GET/POST /api/abtests` | CRUD de experimentos (variantes A/B, epsilon, métricas alvo). |
| A/B Testing | `POST /api/abtests/<id>/start|stop` | Inicia/encerra testes controlando período e promoção de vencedor. |
| Feedback | `POST /api/feedback/ingest` | Recebe quick-replies/NPS, persiste em `feedback_events`, atualiza métricas e gera auditoria. |
| Compliance | `POST /api/compliance/export_data` | Exporta dados mascarados (JSON/CSV) por telefone com auditoria. |
| Compliance | `POST /api/compliance/delete_data` | Executa "right to be forgotten" por telefone, limpando tabelas sensíveis e cache Redis. |
| Compliance | `GET /api/compliance/policies` | Expõe políticas de retenção e TTL configurados por tenant. |

Serviços novos/atualizados:

- `RecommendationService` (motor de insights, triggers, cache e webhook).
- `ABTestService` (gestão de experimentos, epsilon-greedy, métricas agregadas).
- `AuditService` (registro centralizado de auditorias sensíveis).
- Ajustes no `ContextEngine` para incorporar feedback agregado (tom adaptativo e humor).

## Modelos & Migração

- `ABTest` e `ABEvent` com relacionamento 1:N e métricas agregadas por dia (`bucket_date`).
- `FeedbackEvent` registrando canal, tipo de feedback, score e `expires_at` conforme retenção.
- `AuditLog` com `actor`, `action`, `resource`, payload mascarado e tenant obrigatório.
- Migração `0007_business_ai.py` cria as novas tabelas e enum `abtest_status`.

## Métricas Prometheus

- `secretaria_webhook_latency_seconds` – SLO de latência de webhook por tenant.
- `secretaria_whaticket_delivery_success_ratio` – taxa de sucesso acumulada na entrega Whaticket.
- `secretaria_llm_error_rate` – error budget das chamadas ao LLM.

## Ferramentas & Scripts

- `scripts/backup.sh` – faz dump do PostgreSQL (`pg_dump`) e empacota artefatos críticos.
- `scripts/restore.sh` – restaura o dump a partir de um arquivo `.tar.gz` gerado pelo backup.
- Novos comandos Make: `make backup`, `make restore`.

## Painel

- Aba **IA de Negócios** exibe cartões de churn, uso do plano, NPS agregado, lista de testes A/B e formulários para criar/iniciar/parar experimentos.
- Console de compliance com formulários para exportar ou excluir dados por telefone, exibindo políticas vigentes.

## Retenção & LGPD

- Retenção configurável via `RETENTION_DAYS_CONTEXTS`, `RETENTION_DAYS_FEEDBACK`, `RETENTION_DAYS_AB_EVENTS`.
- Exports/Logs mascaram PII (`mask_phone`, `mask_email`, `mask_text`).
- Operações sensíveis geram `AuditLog` automático (recomendações, A/B, feedback, compliance).

## Testes

- Novos testes unitários cobrindo recomendações, A/B testing, ingestão de feedback, rotas de compliance e serviço de auditoria.
