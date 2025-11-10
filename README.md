# Secretaria Virtual Whaticket

[![Release](https://img.shields.io/badge/version-v2.4-blue.svg)](docs/release_v2.4.md)

Arquitetura pronta para produção para uma secretária virtual integrada ao Whaticket com Flask, Redis, RQ e PostgreSQL.

## Visão Geral

- 📄 [Documentação de release v1.0](docs/release_v1.0.md)
- 📄 [Documentação de release v1.1](docs/release_v1.1.md)
- 📄 [Documentação de release v1.2](docs/release_v1.2.md)
- 📄 [Documentação de release v1.3](docs/release_v1.3.md)
- 📄 [Documentação de release v2.0](docs/release_v2.0.md)
- 📄 [Documentação de release v2.1](docs/release_v2.1.md)
- 📄 [Documentação de release v2.2](docs/release_v2.2.md)
- 📄 [Documentação de release v2.3](docs/release_v2.3.md)
- 📄 [Documentação de release v2.4](docs/release_v2.4.md)

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
* **IA de otimização de agenda** com previsão de no-show, reagendamento automático e painel “Insights de Agenda”.
* **Follow-up automático pós-atendimento** com mensagens no WhatsApp, coleta de feedback estruturado e reengajamento direto pelo painel.

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

### v2.4 – Follow-up Automático Pós-Atendimento

1. Follow-up via WhatsApp agendado uma hora após o término da reunião com botões interativos para reengajamento rápido.
2. Interpretação automática de respostas positivas/negativas, criação de `FeedbackEvent` para comentários livres e auditoria em `AuditLog`.
3. Novo painel “Pós-Atendimento” com taxa de resposta, reenviar follow-up e gráfico de satisfação de 30 dias, além de métricas Prometheus dedicadas (`appointment_followups_*`).

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


# Guia Completo: Cal.com Self-Hosted + Integração com Secretária

Este guia vai te ajudar a instalar o Cal.com na sua VPS e conectá-lo com o sistema de secretária.

---

## Parte 1: Instalação do Cal.com Self-Hosted

### Pré-requisitos na VPS

Você precisa ter instalado:
- Docker
- Docker Compose
- Git

### Passo a Passo da Instalação

**1. Clone o repositório Docker do Cal.com:**

```bash
git clone --recursive https://github.com/calcom/docker.git calcom-docker
cd calcom-docker
```

**2. Configure as variáveis de ambiente:**

```bash
cp .env.example .env
nano .env
```

**3. Edite as variáveis importantes no arquivo `.env`:**

```bash
# URL onde o Cal.com vai rodar (troque pelo seu domínio ou IP)
NEXT_PUBLIC_WEBAPP_URL=https://cal.seudominio.com

# Segredo para autenticação (gere um aleatório)
NEXTAUTH_SECRET=sua-chave-secreta-aleatoria-aqui

# URL do banco de dados (o padrão já vem configurado para o Postgres do Docker)
DATABASE_URL=postgresql://unicorn_user:magical_password@database:5432/calendso

# Licença (aceitar termos)
NEXT_PUBLIC_LICENSE_CONSENT=agree

# Telemetria (opcional, pode desabilitar)
CALCOM_TELEMETRY_DISABLED=1
```

**4. Inicie o Cal.com:**

```bash
docker compose up -d
```

**5. Aguarde alguns minutos e acesse:**

```
http://seu-ip:3000
```

Ou se configurou domínio:

```
https://cal.seudominio.com
```

**6. Configure o primeiro usuário:**

Na primeira vez que acessar, você vai criar sua conta de administrador. Preencha:
- Nome
- Email
- Senha
- Nome de usuário (ex: `admin`)

---

## Parte 2: Gerar API Key no Cal.com

Após instalar e criar sua conta:

**1. Faça login no Cal.com**

**2. Vá em Settings (Configurações)**

**3. Clique em "Security" (Segurança)**

**4. Role até "API Keys"**

**5. Clique em "New API Key" (Nova chave de API)**

**6. Dê um nome para a chave:** `secretaria-virtual`

**7. Copie a chave gerada** - ela vai começar com `cal_` ou `cal_live_`

**IMPORTANTE:** Guarde essa chave em local seguro! Você não conseguirá vê-la novamente.

---

## Parte 3: Configurar a Secretária para Usar o Cal.com

Agora você precisa configurar o sistema de secretária para se comunicar com o Cal.com.

### Configuração do .env para Cal.com Self-Hosted

Adicione ao seu arquivo `.env`:

```bash
# URL da API do Cal.com (sua instância self-hosted)
CAL_API_BASE_URL=https://cal.seudominio.com/api/v1

# Dias de antecedência para buscar disponibilidade
CAL_DEFAULT_DAYS_AHEAD=7
```

Depois configure no banco de dados:

```sql
UPDATE companies 
SET 
  cal_api_key = 'cal_live_xxxxxxxxxx',
  cal_default_user_id = 1,
  cal_webhook_secret = 'seu-webhook-secret'
WHERE id = 1;
```

### 3.1 Configurar no Banco de Dados

Você precisa adicionar as configurações do Cal.com na tabela `companies` do seu banco de dados.

**Opção 1: Via SQL direto no PostgreSQL**

```sql
-- Conecte no banco de dados da secretária
psql -U seu_usuario -d secretaria_db

-- Atualize a empresa com as credenciais do Cal.com
UPDATE companies 
SET 
  cal_api_key = 'cal_live_xxxxxxxxxxxxxxxxxx',  -- Sua API key
  cal_default_user_id = 1,  -- ID do seu usuário no Cal.com (geralmente 1)
  cal_webhook_secret = 'seu-webhook-secret-aqui'  -- Pode gerar um aleatório
WHERE id = 1;  -- ID da sua empresa
```

**Opção 2: Via Painel Web**

Se o seu painel web (`/painel`) permite editar empresas, você pode adicionar essas informações lá.

### 3.2 Descobrir seu User ID no Cal.com

Para descobrir seu `cal_default_user_id`:

**Método 1: Via API**

```bash
curl "https://cal.seudominio.com/api/v1/users?apiKey=cal_live_xxxxxxxxxx"
```

Procure pelo seu usuário e pegue o `id`.

**Método 2: Via Banco de Dados do Cal.com**

```bash
# Conecte no container do Cal.com
docker exec -it calcom-docker-database-1 psql -U unicorn_user -d calendso

# Liste os usuários
SELECT id, email, username FROM users;
```

### 3.3 Configurar Webhook (Opcional mas Recomendado)

O webhook permite que o Cal.com notifique sua secretária quando eventos são criados/cancelados.

**No Cal.com:**

1. Vá em Settings > Developer > Webhooks
2. Clique em "New Webhook"
3. Configure:
   - **Subscriber URL:** `https://sua-secretaria.com/api/agenda/webhook/cal`
   - **Event Triggers:** Marque `booking.created`, `booking.rescheduled`, `booking.cancelled`
   - **Secret:** Use o mesmo `cal_webhook_secret` que você configurou no banco

---

## Parte 4: Testar a Integração

### 4.1 Verificar se a Secretária Consegue Acessar o Cal.com

Você pode testar enviando uma mensagem para a secretária via WhatsApp:

```
"Gostaria de agendar uma reunião"
```

A secretária deve:
1. Consultar os horários disponíveis no Cal.com
2. Mostrar opções de horário
3. Permitir que você escolha
4. Criar o evento no Cal.com

### 4.2 Verificar Logs

Para ver se está funcionando, verifique os logs da secretária:

```bash
# Logs do worker
docker logs -f secretaria-worker-1

# Ou se estiver usando PM2
pm2 logs secretaria-worker
```

Procure por mensagens como:
- `agenda_availability_success`
- `agenda_booking_created`

---

## Parte 5: Configurações Avançadas (Opcional)

### 5.1 Configurar Nginx como Proxy Reverso

Se você quer usar um domínio (ex: `cal.seudominio.com`), configure o Nginx:

```nginx
server {
    listen 80;
    server_name cal.seudominio.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```

Depois configure SSL com Let's Encrypt:

```bash
sudo certbot --nginx -d cal.seudominio.com
```

### 5.2 Configurar Tipos de Evento

No Cal.com, você pode criar diferentes tipos de reunião:

1. Vá em "Event Types"
2. Crie tipos como:
   - "Reunião Inicial - 30min"
   - "Consultoria - 1h"
   - "Follow-up - 15min"

Cada tipo pode ter duração e disponibilidade diferentes.

---

## Resumo das Variáveis Importantes

| Variável | Onde Configurar | Exemplo |
|----------|----------------|---------|
| `cal_api_key` | Banco de dados da secretária | `cal_live_xxxxxxxxxx` |
| `cal_default_user_id` | Banco de dados da secretária | `1` |
| `cal_webhook_secret` | Banco de dados da secretária | `meu-secret-123` |
| `NEXT_PUBLIC_WEBAPP_URL` | `.env` do Cal.com | `https://cal.seudominio.com` |
| `NEXTAUTH_SECRET` | `.env` do Cal.com | String aleatória |

---

## Troubleshooting

### Problema: "Agenda automática não configurada"

**Solução:** Verifique se `cal_api_key` e `cal_default_user_id` estão configurados no banco.

### Problema: "Não consegui acessar a agenda"

**Solução:** 
1. Verifique se o Cal.com está rodando: `docker ps`
2. Teste a API manualmente: `curl "http://localhost:3000/api/v1/users?apiKey=sua-chave"`

### Problema: Cal.com não inicia

**Solução:**
1. Verifique os logs: `docker compose logs -f`
2. Verifique se o PostgreSQL está rodando
3. Verifique se as variáveis do `.env` estão corretas

---

## Próximos Passos

Após configurar tudo:

1. ✅ Teste enviando mensagens para a secretária
2. ✅ Verifique se os agendamentos aparecem no Cal.com
3. ✅ Configure lembretes automáticos (já está implementado na secretária)
4. ✅ Personalize os tipos de evento no Cal.com

Qualquer dúvida, consulte a documentação oficial: https://cal.com/docs/self-hosting/docker


## Limitações

* LLM Gemini precisa de chave válida e rede externa.
* Migrações devem ser executadas antes do primeiro uso em produção.
