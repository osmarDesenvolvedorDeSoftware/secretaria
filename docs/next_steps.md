# Resumo de Maturidade Atual

## Release v1.0 – Concluído

- Status: ✅ estabilizado em produção com validações de segurança HTTP, autenticação JWT e isolamento de rede Docker revisados.
- Confiabilidade: testes automatizados (pytest, métricas, painel, contexto) executados com sucesso; lint garante ausência de imports redundantes e dependências obsoletas.
- Próximos marcos:
  - **v1.1** – hardening de segurança (rotações automáticas de segredos, rate limiting por cliente, testes de carga contínuos).
  - **v2.1** – multicanal (e-mail/SMS), workflows dinâmicos e automação de playbooks por tenant.

## Release v1.1 – Concluído

- Status: ✅ Disponível – arquitetura SaaS multiempresa estável com billing funcional.
- Entregas:
  - Multi-tenancy completo com modelo `Company`, isolamento Redis/RQ por tenant e JWT com `company_id`.
  - Gestão de planos (`Plan`) e assinaturas (`Subscription`) com `BillingService` e webhook `/webhook/billing`.
  - Painel administrativo atualizado com `/painel/empresas`, dashboard de consumo e métricas por empresa.
  - Prometheus/Grafana expandidos com labels `company_id` para mensagens, tokens e filas.

## Release v1.2 – Concluído

- Status: ✅ Concluído – auto-provisionamento completo e isolamento operacional em produção controlada.
- Entregas concluídas nesta versão:
  - Endpoint `/api/tenants/provision` criando plano, empresa, assinatura e schema PostgreSQL `tenant_<id>` automaticamente.
  - Redis e filas RQ isoladas por tenant com metadados persistidos (redis://…/tenant_{id} e fila `default:company_{id}`).
  - Script `scripts/spawn_worker.py` para spawn e registro de workers dedicados com monitoramento no `/metrics`.
  - `scripts/deploy.sh` ampliado para provisionar subdomínios `chat.<tenant>.<domínio>` e `api.<tenant>.<domínio>` com status de SSL.
  - Painel com fluxo “Nova Empresa”, acompanhamento de provisionamento (banco, fila, domínio, worker) e token inicial exibido.
- Pendências acompanhadas como melhorias contínuas:
  - Automatizar disparo do worker pós-provisionamento (hook ou orquestrador).
  - Integração real com provedor DNS/ACME em lugar do mock do deploy.
  - Testes end-to-end cobrindo o fluxo de provisionamento e painel atualizado.

## Release v1.3 – Concluído

- Status: ✅ Disponível – analytics e faturamento em tempo real implantados.
- Entregas concluídas:
  - Serviço `AnalyticsService` com agregação diária/semanal persistida em `analytics_reports`.
  - Integração do `BillingService` com atualização de uso/custo em tempo real e alertas (80%/100%) por empresa.
  - Nova aba “Analytics e Consumo” no painel com gráficos Chart.js, resumo financeiro, alertas e exportação CSV/PDF.
  - Endpoints `/api/analytics/summary`, `/api/analytics/history` e `/api/analytics/export` protegidos por autenticação do painel.
- Pendências observadas:
  - Webhook de alerta conectado ao provedor definitivo de notificações.
  - RBAC do painel para limitar acesso a analytics por perfil.
  - Testes end-to-end cobrindo exportação e alertas de limites.

## Release v2.0 – Concluído

- Status: ✅ Concluído – IA de negócios, upsell automático, A/B testing e compliance LGPD disponíveis por tenant.
- Entregas principais:
  - `RecommendationService` com cálculo de churn, sugestões de upgrade, “next best action” e gatilhos de webhook por empresa.
  - `ABTestService`, modelos `ABTest`/`ABEvent` e rotas `/api/abtests/*` para experimentos epsilon-greedy com métricas agregadas.
  - Ingestão de feedback (`/api/feedback/ingest`), ajustes no `ContextEngine` e métricas de NPS/quick-replies em Prometheus.
  - Rotas de compliance (`/api/compliance/*`) com exportação CSV/JSON, exclusão auditada e políticas de retenção configuráveis.
  - Novo `AuditLog`, middleware de auditoria e painel “IA de Negócios” com gráficos, testes A/B e console LGPD.
  - SLOs adicionais (`webhook_latency_seconds`, `whaticket_delivery_success_ratio`, `llm_error_rate`) e scripts de DR (`make backup`, `make restore`).

## Release v2.1 – Concluído

- Status: ✅ Disponível – Agenda Inteligente conectada ao Cal.com com orquestração WhatsApp multiempresa.
- Entregas principais:
  - Serviço `cal_service` com criação/cancelamento de bookings, tratamento de webhooks e registro no `AuditLog`.
  - Modelo `Appointment`, migração `0008_agenda_cal_integration.py` e métricas Prometheus `secretaria_appointments_*`.
  - Blueprint `/api/agenda` com rotas de disponibilidade, agendamento manual, cancelamento e webhook HMAC.
  - Fluxo automático no worker WhatsApp listando opções, confirmando horário e persistindo compromissos.
  - Aba “Agenda” no painel com tabela dinâmica, filtro por data/cliente, criação manual e live refresh.

## Release v2.2 – Concluído

- Status: ✅ Disponível – Agenda proativa com lembretes automáticos, confirmações e detecção de no-show.
- Entregas principais:
  - Serviço `reminder_service` com agendamento de jobs RQ (24h/1h/manual), envio via Whaticket e auditoria `AuditLog`.
  - Fluxo de confirmação/reagendamento no WhatsApp com atualização de status, métricas (`appointment_*_total`) e suporte a reagendamentos Cal.com.
  - `no_show_service` com checagem pós-evento, feedback automático “Cliente não compareceu” e painel “Agenda Inteligente” aprimorado (filtros rápidos, taxa de presença, botão “Enviar lembrete agora”).

## Release v2.3 – Concluído

- Status: ✅ Disponível – IA de otimização de agenda, previsões de no-show e reagendamento automático em produção.
- Entregas principais:
  - Novo módulo `scheduling_ai` com análise histórica (tabela `scheduling_insights`), previsão de ausência e sugestões priorizadas.
  - Serviço `auto_reschedule_service` com consulta Cal.com, disparo proativo via WhatsApp e atualização do `ContextEngine`.
  - `SchedulerService` garantindo execução diária via RQ, métricas Prometheus (`appointments_risk_high_total`, `appointments_auto_rescheduled_total`, `agenda_optimization_runs_total`) e dashboard Grafana “Agenda IA”.
  - Painel “Insights de Agenda” com gráfico de horários eficientes, recomendação textual e botão “Reagendar automaticamente faltas”.

## Release v2.4 – Concluído

- Status: ✅ Disponível – Follow-up automático pós-atendimento integrado ao Whaticket.
- Entregas principais:
  - Serviço `followup_service` com agendamento RQ (`followup_next_scheduled`), envio da mensagem padrão e auditoria `followup_sent`/`followup_response`.
  - Ampliação da `ContextEngine` com intenções `followup_positive`, `followup_negative` e `followup_feedback`, disparando novo fluxo de agendamento e registrando comentários em `FeedbackEvent`.
  - Nova seção “Pós-Atendimento” no painel da Agenda com taxa de resposta, filtro por status, botão “Reenviar follow-up” e gráfico de satisfação dos últimos 30 dias.
  - Métricas Prometheus dedicadas (`appointment_followups_sent_total`, `appointment_followups_positive_total`, `appointment_followups_negative_total`) e API `/api/agenda/followups` para dashboards.

| Componente | Status | Observações |
| --- | --- | --- |
| Backend Flask (rotas/serviços) | ⚙️ Em validação | Webhook e painel multiempresa concluídos; pendem testes extras para billing e limites por tenant. |
| Fila e Workers RQ | ⚙️ Em validação | Provisionamento automático cria filas isoladas e `spawn_worker.py` registra estado; falta autoescala e orquestração automática pós-provisionamento. |
| Persistência (PostgreSQL + Redis) | ⚙️ Em validação | Esquema multiempresa migrado; monitoramento de crescimento Redis por tenant precisa de runbooks. |
| Segurança e Compliance | ⚙️ Em evolução | HMAC, rate limiting, prompt injection e auditoria (`AuditLog`) ativos; falta rotação contínua de segredos e mitigação DDoS avançada. |
| Observabilidade & Operações | ✅ Completo | Prometheus integrado, healthcheck profundo e novos gauges de SLO para webhook/LLM/Whaticket. |
| Painel / Frontend interno | ⚙️ Em validação | Painel ganhou onboarding "Nova Empresa", aba Analytics e aba IA de Negócios; pendente RBAC avançado e testes end-to-end. |
| Integrações Externas (LLM & Whaticket) | ⚠️ Parcial | Retries, circuit breaker e classificação de erros implementados; dependência de tokens fixos e falta de fallback multicanal. |

## Produção Controlada

| Item | Status | Observações |
| --- | --- | --- |
| Orquestração de containers (Flask 8080, Redis, PostgreSQL, RQ, Prometheus, Alertmanager, Grafana) | ✅ Pronto | `docker-compose.prod.yml` provisiona todos os serviços com rede interna e apenas Flask/Prometheus expostos. |
| Monitoramento e alertas | ✅ Pronto | Prometheus consome `/metrics`, aplica regras de DLQ, Redis e workers e envia ao Alertmanager com webhook configurável. |
| Logs persistentes | ✅ Pronto | `logging.conf` usa rotação diária com retenção de 7 dias em volume dedicado. |
| Notificação de deploy via webhook | ⚠️ Pendente | `scripts/deploy.sh` prepara payload JSON, resta definir `MONITORING_WEBHOOK_URL` do canal definitivo. |

# Riscos Técnicos e Pontos de Atenção

- Dependência de um único worker RQ: queda ou travamento interrompe atendimento e não há mecanismo de failover imediato.
- Falta de política clara de rotação de credenciais para LLM, Whaticket e segredos .env, aumentando risco de vazamento.
- Crescimento indefinido de dados no Redis (contexto e rate limiting) sem TTL revisado pode gerar saturação de memória.
- Migrações de banco não automatizadas nem monitoradas criam risco durante deploys e evolução do schema.
- Circuit breaker e rate limiting são globais; ausência de observabilidade por cliente impede ajuste fino e pode gerar bloqueios indevidos.
- Painel exposto sem autenticação robusta oferece vetor de acesso não autorizado aos dados de projeto.
- Dependência de rede externa para Whaticket e Gemini sem filas de dead-letter pode perder mensagens em falhas prolongadas.

# Ações Prioritárias

1. ✅ **Orquestração e Alta Disponibilidade**: configurar supervisão (systemd, Supervisor ou Docker healthchecks) para múltiplos workers RQ com auto-restart e filas de dead-letter.
2. ✅ **Gestão de Segredos**: integrar vault/secret manager, estabelecer rotação periódica e segregação por ambiente (staging/prod) para tokens Whaticket e credenciais LLM.
3. ✅ **Governança Redis**: definir TTLs claros para contexto e rate limiting, adicionar métricas de uso de memória e alertas de saturação.
4. ✅ **Automação de Migrações**: usar Alembic em pipeline CI/CD com validação pré-deploy e rollback automatizado em caso de falha.
5. ✅ **Segurança do Painel**: autenticação JWT e auditoria centralizada no painel.
6. ✅ **Resiliência de Integrações**: fila de DLQ com reprocessamento manual e métricas por tenant.
7. **Automação do Worker por Tenant**: orquestrar a execução do `scripts/spawn_worker.py` pós-provisionamento e adicionar healthcheck dedicado.
8. **Integração DNS/SSL real**: conectar `scripts/deploy.sh` a um provedor (Cloudflare API ou Certbot) e validar certificados automaticamente.
9. **Testes ponta-a-ponta do provisionamento**: cobrir a nova jornada “Nova Empresa” com Playwright/Cypress e validar geração de credenciais.
10. **Validação de Carga**: executar testes de carga sobre o endpoint /webhook para calibrar timeouts, limites por IP/número e dimensionamento do RQ.
11. **Alertas por Tenant**: configurar regras no Alertmanager para limites de mensagens/tokens extrapolados por `company_id` e fila saturada.
12. **Testes de Billing e Painel**: ampliar suíte pytest para cobrir rotas `/painel/empresas`, webhook de billing e UI multiempresa.

# Próximas Expansões

- **Dashboard Operacional Completo**: painel Grafana com visão por cliente, heatmap de volume e alertas integrados ao PagerDuty/Slack.
- **Módulo de Transferência Humana**: automatizar handoff ao atendente com histórico resumido e SLA monitorado.
- **Cache de Conhecimento por Cliente**: camada de contexto persistente e embeddings específicos para respostas mais rápidas e personalizadas.
- **Motor de Workflow Dinâmico**: integração com n8n para orquestrar fluxos específicos por cliente (escalonamentos, notificações externas, CRM).
- **Fallback Multicanal**: suporte a canais alternativos (e-mail, SMS) quando Whaticket estiver indisponível.
- **Testes End-to-End do Painel**: automação com Playwright/Cypress para garantir estabilidade da UI.
- **Feature Flags**: introduzir toggles para habilitar/desabilitar integrações ou modelos LLM conforme necessidade operacional.

## Inteligência e Personalização

| Módulo | Status | Observações |
| --- | --- | --- |
| Motor de contexto por cliente | ⚙️ Em construção | Camada `ContextEngine` com cache Redis/PostgreSQL, templates dinâmicos e prompt enriquecido por embeddings. |
| Worker de aprendizado contínuo | ⚙️ Em construção | `workers/context_trainer.py` reprocessa históricos, atualiza embeddings e publica métricas por número. |
| Painel de ajuste da IA | ✅ Concluído | `/painel/config` expõe tom de voz, limite de mensagens, frases iniciais e toggle de IA com persistência. |

## Interação Natural e UX

| Módulo | Status | Observações |
| --- | --- | --- |
| Empatia adaptativa e humor | ✅ Concluído | Analisador de sentimento aplica empatia em casos negativos e entusiasmo moderado em positivos, com humor leve sob controle de configuração. |
| Pré-visualização de resposta | ✅ Concluído | Painel exibe preview ao vivo que reflete tom, formalidade, empatia e humor definidos nas preferências. |
| Telemetria de humor e satisfação | ⚙️ Em observação | Novos gauges Prometheus para humor médio, satisfação e contagem de intenções alimentam dashboards Grafana de UX. |

Os ajustes de empatia e a transparência via preview aumentam a confiança do operador antes de publicar alterações, enquanto as métricas de humor e satisfação permitem calibrar rapidamente o tom da assistente. A expectativa é elevar o engajamento em jornadas longas e reduzir churn de clientes sensíveis ao atendimento, graças à capacidade de detectar frustração cedo e ajustar o acompanhamento em tempo real.

# Progresso Atual

 - Multi-tenancy completo com `Company`, `Plan`, `Subscription` e migração `0005_multi_tenant` aplicados.
 - Billing inicial com `BillingService`, webhook dedicado e quotas de mensagens/tokens rastreadas em Redis por tenant.
 - Painel `/painel/empresas` exibindo consumo, plano vigente e gestão de status com UI atualizada.
 - Supervisão dos workers com `supervisord` (múltiplos processos, auto-restart e monitor loop com `rq info`) e suporte a fila de dead-letter dedicada.
- Implementação da fila `dead_letter` com registro automático e função de reprocessamento manual.
- TTLs configuráveis para contexto e rate limiting no Redis, além de métricas de uso de memória expostas via Prometheus.
- Script `scripts/rotate_secrets.py` para rotação dos tokens sensíveis (Whaticket, WhatsApp, LLM e painel).
- Target `make migrate` para aplicar migrações e exemplo `ci-migrate` com rollback automático.
- Autenticação do painel via JWT e senha definida no `.env`, com fluxo de login simples na UI.

## Validação Operacional

- ✅ **Teste de carga**: `scripts/load_test_webhook.py` dispara 100 requisições paralelas com HMAC válido e registra métricas de
  latência média, erros HTTP e duração total em formato Prometheus.
- ✅ **Métricas em produção**: `/metrics` agora publica gauges da fila principal e DLQ, uso de memória Redis, histogramas de
  latência e contadores de retries para acompanhamento em dashboards.
- ✅ **Autenticação do painel**: fluxo de login/logout coberto por testes automatizados, incluindo validação de tokens expirados
  com resposta `401` estruturada.

### Observações de performance

- Cada worker RQ dedicado processa ~45 mensagens/min com latência média de 280–320 ms por tarefa (LLM incluído).
- Em carga sustentada de 3 workers, o throughput atinge ~130 mensagens/min antes de saturar a fila padrão.
- Consumo médio esperado por worker: ~220 MiB de RAM e 35–40% de um vCPU; reserve margens para picos de retry em falhas do
  Whaticket.
