# Internal Architecture Explained

## 1. Visão geral da arquitetura

### Componentes e estrutura modular
A aplicação Flask está organizada em camadas explícitas dentro do diretório `app/`: rotas HTTP ficam em `app/routes/`, serviços de domínio em `app/services/`, modelos SQLAlchemy em `app/models/` e workers utilitários em `app/workers/`. O módulo de inicialização (`app/__init__.py`) centraliza a configuração de logging estruturado com `structlog`, prepara o engine SQLAlchemy, cria a `scoped_session` e injeta clientes Redis e filas RQ na instância Flask. Também expõe blueprints de módulos como webhooks, agenda e analytics, além de métricas Prometheus agregadas por tenant.

### Papel de cada camada
- **API Flask**: recebe webhooks e chamadas administrativas, injeta `TenantContext` por domínio e cria correlation IDs para rastreabilidade.
- **Serviços**: encapsulam regras de negócio (ex.: `TaskService` orquestra processamento de mensagens, `CalService` integra com Cal.com, `BillingService` e `AnalyticsService` calculam uso).
- **Modelos**: persistem conversas, contexto personalizado, eventos de auditoria e agendamentos em PostgreSQL.
- **Workers RQ**: processam mensagens assíncronas, reprocessam dead-letters, treinam perfis de contexto e executam rotinas agendadas via scheduler interno.
- **Redis**: mantém filas, caches de contexto curto, circuit breaker de LLM e rate limits.
- **PostgreSQL**: armazena contexto de longo prazo, agenda, billing, auditoria e configurações multi-tenant.
- **Prometheus/Grafana**: coletam métricas expostas pela rota `/metrics`, com gauges/counters por empresa para filas, LLM, agenda e atendimento.

### Inicialização da aplicação
`init_app()` configura logging e, em seguida, instancia engine SQLAlchemy e Redis. Ele guarda fábricas de sessão e conexões Redis no objeto Flask, injeta caches de filas por tenant (principal e dead-letter) e registra serviços compartilhados (`AnalyticsService`, `BillingService`, `SchedulerService`). O hook `before_request` resolve o domínio do tenant, vincula `TenantContext` e correlation IDs, enquanto `after_request` inclui cabeçalhos de segurança e registra métricas de latência por requisição. Ao subir, o scheduler garante o agendamento diário de otimização da agenda, enquanto a rota `/metrics` consolida métricas de filas, workers e uso de Redis por companhia.

## 2. Fluxo principal de atendimento WhatsApp

1. **Recepção do webhook**: `POST /webhook/whaticket` valida assinatura HMAC e token de webhook antes de parsear o corpo via `IncomingWebhook`. Falhas incrementam contadores Prometheus específicos (401/400/404) e encerram o fluxo.
2. **Contexto do tenant e rate limiting**: o domínio resolve o `TenantContext`. O `RateLimiter` bloqueia IPs ou números acima do limite por janela e atualiza métricas de webhook.
3. **Normalização e sanitização**: números são padronizados com DDI `55` e o texto é limpo para remover caracteres não permitidos.
4. **Enfileiramento**: `TaskService.enqueue()` coloca o job em uma fila RQ específica do tenant, configurando política de retry e anexando metadados (number, kind, correlation ID). O dead-letter é cacheado por tenant para reprocesso manual.
5. **Processamento assíncrono** (`process_incoming_message`):
   - Recupera serviços compartilhados e inicializa `TaskService` específico do job.
   - Valida o payload, contabiliza métricas de uso (mensagens, tokens) e atualiza billing/analytics.
   - `ContextEngine.prepare_runtime_context()` carrega histórico curto do Redis (cache) ou longo do PostgreSQL (`Conversation.context_json`), detecta intenção, sentimento e feedbacks, monta o `system_prompt` e aplica regras de tom personalizadas.
   - Fluxos especiais de agenda (_handle_agenda_flow_) interceptam intenções de agendamento, reagendamento ou follow-up, consultando Cal.com e estado da agenda em Redis.
   - Caso não seja fluxo especial, verifica tentativa de prompt injection, aplica fallback se a IA estiver desabilitada, e chama a camada de LLM.
6. **Geração de resposta**: `LLMClient.generate_reply()` envia mensagens recentes e prompt para o provedor Gemini com circuit breaker e retries exponenciais. Respostas vazias acionam templates de fallback.
7. **Personalização final**: a resposta é renderizada com o template selecionado (A/B testing opcional) e adicionada ao histórico curto e longo.
8. **Entrega**: `WhaticketClient.send_text()` envia a mensagem ao WhatsApp via Whaticket, com retry exponencial e métricas de latência/erro. Dead-letters recebem payload completo para diagnóstico.
9. **Persistência**: histórico curto é salvo em Redis (TTL) e consolidado no PostgreSQL, enquanto `DeliveryLog` registra status e IDs externos. Métricas de tempo de processamento são observadas no histograma `task_latency_seconds`.
10. **Logs e métricas**: todo o pipeline usa `structlog` com correlation IDs; counters/gauges de webhook, LLM, billing, agenda e entrega são atualizados por tenant.

### Contexto curto vs. longo
- **Contexto curto (Redis)**: mensagens recentes ficam armazenadas com TTL configurável para respostas rápidas e controle de tokens. É atualizado via `save_history`, reidratado em cada requisição e usado para montar prompts.
- **Contexto longo (PostgreSQL)**: `Conversation.context_json` mantém histórico persistente, útil para retomada após expiração do cache. O `ContextTrainer` worker recalcula perfis e embeddings com base nessas conversas.

## 3. Camada de IA

### Serviço LLM
`services/llm.py` implementa `LLMClient` com circuit breaker por tenant, retry exponencial via Tenacity e coleta de métricas (`llm_latency`, `llm_errors`, `token_usage_total`). Antes de chamar o provedor, ele avalia prompt injection e retorna mensagem segura se detectar tentativa de manipulação. Respostas válidas atualizam contadores de tokens e error budget por empresa.

### Montagem de contexto
`ContextEngine` normaliza e tokeniza mensagens, detecta intenção (agenda, follow-up, dúvida, etc.), calcula sentimento heurístico e sintetiza histórico em frases curtas. Também consulta feedbacks, atualiza gauges de satisfação/sentimento no Redis/Prometheus e ajusta o tom (formalidade, empatia, humor) com base no perfil salvo. Templates são escolhidos conforme intenção e sentimento, com fallback garantido.

### Anti prompt-injection, tokenização e retries
Além da checagem em `LLMClient`, o `TaskService` volta ao template `fallback` quando detecta injection após a etapa de agenda, garantindo rastreamento métrico. Tokens estimados são contados em ambas as direções para billing e analytics. Retries configuráveis no RQ evitam perda de jobs, com dead-letter após tentativas máximas.

### Gemini x OpenAI
- **Gemini**: provedor padrão para geração, usando `gemini-pro:generateContent` com chave por tenant. Circuit breaker controla indisponibilidades temporárias.
- **OpenAI**: atualmente usado para embeddings personalizados quando `EMBEDDING_PROVIDER=openai`, via API `text-embedding-3-small`. Ambos os caminhos fazem hash fallback em caso de falha, garantindo atualização de perfis mesmo sem conectividade total.

### Aprendizado de intenções, humor e personalização
`ContextEngine.prepare_runtime_context()` usa heurísticas e contagens de feedback armazenadas para rotular intenções e ajustar tom. O worker `context_trainer.py` reprocessa conversas históricas, gera embeddings e atualiza tópicos/produtos de interesse, alimentando personalização contínua.

## 4. Agenda inteligente

### Fluxo integrado ao Cal.com
1. **Detecção de intenção**: intenção `appointment_request` ou follow-ups positivos direcionam para `_handle_agenda_flow`.
2. **CalService**: lista disponibilidade (`listar_disponibilidade`) usando credenciais do tenant, constrói opções e registra auditoria de acesso.
3. **Confirmação**: usuário escolhe opção ou responde livremente; o serviço valida seleção e monta mensagem humana caso falte configuração.
4. **Criação**: `criar_agendamento` chama a API `/bookings`, gera métricas (`appointments_total`, `appointments_latency_seconds`) e salva `Appointment` no banco com status `pending`.
5. **Webhook de retorno**: callbacks do Cal.com atualizam status, permitindo confirmações/alterações via `Appointment`.
6. **Follow-up**: `ReminderService` agenda lembretes padrão (24h e 1h) e customizados; `NoShowService` verifica ausência pós-evento e cria feedbacks; `FollowupService` dispara pós-atendimento, registra respostas e atualiza métricas (`appointment_followups_*`).
7. **Ciclo contínuo**: `SchedulingAI` roda diariamente via scheduler, calcula mapa de calor de horários, marca slots de alto risco (`appointments_risk_high_total`) e gera recomendações armazenadas em `SchedulingInsights`.

### Métricas Prometheus
- Contadores de agendamentos criados, confirmados, cancelados e reagendados.
- Histogramas de latência de criação.
- Counters para lembretes, follow-ups e no-shows.
- Gauge `agenda_optimization_runs_total` incrementado pelo job diário.

## 5. Serviços complementares

- **BillingService**: computa custo estimado por mensagens/tokens, sincroniza com `AnalyticsService` e mantém hash de uso em Redis para alertas em tempo real.
- **ProvisionerService**: automatiza criação de tenants (schema dedicado, fila/Redis namespaced, plano e assinatura) e grava metadados de infraestrutura/domínio no Redis. Gera token de painel e orienta spawn de worker dedicado.
- **RecommendationService**: combina uso atual, limites do plano, custos e feedbacks para gerar insights de churn, upsell e próxima melhor ação; armazena em Redis com TTL e emite webhooks.
- **AnalyticsService**: agrega uso por granularidade (daily/weekly), calcula custo e mantém dashboards em tempo real via Redis.
- **AuditService e LGPD**: `AuditService` registra toda ação sensível (agenda, follow-ups, compliance). A rota de compliance oferece exportação/anônimo e deleção de dados por número, garantindo rastreabilidade e aderência à LGPD.

## 6. Infraestrutura e processos

- **Containers**: `docker-compose.prod.yml` define serviços `app`, `worker`, `redis`, `postgres`, `prometheus`, `grafana` e `alertmanager`, todos conectados à rede interna; `app` expõe porta 8080 e monta volume de logs.
- **Comunicação**: containers compartilham rede Docker; o app acessa Redis/Postgres via hostnames internos; Prometheus coleta métricas da aplicação/worker; Grafana consome Prometheus.
- **Workers e jobs**: `rq_worker.py` conecta-se ao Redis e processa filas por tenant; dead-letter é persistido com payload e motivo. O monitoramento consulta Redis para contar workers e filas.
- **Healthchecks e métricas**: `/metrics` expõe gauges para tamanho de fila, dead-letter, workers ativos, memória Redis e distribuição por tenant. Alertmanager pode consumir esses dados para incidentes.
- **Scheduler interno**: `SchedulerService.ensure_daily_agenda_optimization()` registra job diário que enfileira tarefas `scheduling_ai` por tenant, armazenando timestamp em Redis para evitar duplicidade. Outros scripts (ex.: `context_trainer`) podem ser orquestrados via cron externo.

## 7. Multi-tenant e isolamento

- **Mapeamento**: cada empresa possui `company_id`, domínio exclusivo e schema lógico (`tenant_{id}`) criado pelo Provisioner. Redis e filas usam prefixos `company:{id}` para isolamento.
- **Segmentação de métricas/logs**: todas as métricas Prometheus incluem label `company`; logs `structlog` recebem `company_id` via `contextvars`. Dead-letters, billing hashes, circuit breakers e caches vivem em namespaces segregados.
- **Provisionamento de workers**: Provisioner armazena hint de comando para subir worker dedicado (`spawn_worker.py --company-id`). Workers registram-se em Redis e são contados pela rota `/metrics`.
- **Separação de dados**: contextos curtos/longos, agenda, analytics e auditoria filtram por `company_id`, garantindo isolamento lógico mesmo compartilhando infraestrutura.

## 8. Observabilidade e auditoria

- **Logging estruturado**: `configure_logging()` habilita `structlog` com timestamps ISO, nível e stacktrace. `before_request` cria correlation IDs e associa `company_id`. Logs de tarefas incluem `job_id`, tentativas e metadados de entrega.
- **Métricas**: `/metrics` publica counters/histogramas para webhooks, filas, LLM, tokens, entregas Whaticket, agenda (lembretes, follow-ups, no-shows), uso de Redis, workers e healthchecks. Essas métricas alimentam dashboards e alertas.
- **Prometheus/Grafana**: Prometheus coleta `app` e `worker`; Grafana provê dashboards provisionados. Alertmanager permite webhooks customizados para incidentes de fila, latência ou erros LLM.
- **Auditoria**: ações críticas (agendamentos, lembretes, follow-ups, compliance) são registradas em `AuditLog`. Exportações LGPD mascaram telefone, registram ator e IP e permitem deleção controlada com confirmação. Investigadores podem traçar o atendimento pelo correlation ID e eventos no banco/audit trail.

---

Este documento descreve os mecanismos internos da Secretaria Virtual v2.4, cobrindo como os componentes colaboram para oferecer atendimento multicanal inteligente com observabilidade, automação de agenda e governança de dados.
