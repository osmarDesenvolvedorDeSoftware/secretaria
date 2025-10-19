# Guia de Deploy, Configuração e Uso — Secretária Virtual v2.4

Este documento consolida todas as etapas para preparar um servidor Ubuntu 24.04, realizar o deploy da Secretária Virtual (Flask + PostgreSQL + Redis + RQ + Prometheus + Grafana), integrar Whaticket, Cal.com e provedores LLM (Gemini ou OpenAI), além de validar o funcionamento em produção e testes.

## 1. Requisitos de infraestrutura

| Ambiente | CPU | RAM | Armazenamento | Observações |
| --- | --- | --- | --- | --- |
| **Produção multi-tenant** | 4 vCPU | 8 GB | 120 GB SSD | Ideal para rodar API, worker, banco e monitoramento na mesma VPS.
| **Homologação/Testes** | 2 vCPU | 4 GB | 60 GB SSD | Possível reutilizar snapshots da produção e ajustar limites de tráfego.

**Portas expostas:**
- 8080 (Flask API / painel)
- 5432 (PostgreSQL)
- 6379 (Redis)
- 9090 (Prometheus)
- 3000 (Grafana)
- 9093 (Alertmanager, apenas rede interna)

Restrinja o acesso público apenas às portas 80/443 para o proxy reverso. As demais devem estar protegidas via firewall/local.

## 2. Estrutura de pastas recomendada

Organize o servidor com o usuário `secretaria` (sem privilégios de root) e a seguinte estrutura:

```
/home/secretaria/
├── app/                # Código da Secretária Virtual
├── data/
│   ├── postgres/
│   └── redis/
├── monitoring/
│   ├── prometheus/
│   └── grafana/
├── backups/
└── logs/
```

Clone o repositório em `~/app` e mantenha `docker-compose.prod.yml` na raiz do projeto.【F:docker-compose.prod.yml†L1-L89】

## 3. Preparação inicial do servidor Ubuntu 24.04

1. Atualize pacotes e instale dependências básicas:
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y docker.io docker-compose-plugin make python3.11-venv curl jq git ufw
   sudo usermod -aG docker secretaria
   ```
2. Configure firewall permitindo apenas SSH (22) e HTTP/HTTPS:
   ```bash
   sudo ufw allow OpenSSH
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```
3. Faça login como usuário `secretaria` antes de continuar (`sudo su - secretaria`).

## 4. Variáveis de ambiente (`.env`)

O repositório fornece `.env.example` com os principais parâmetros.【F:.env.example†L1-L24】 Copie e personalize:

```bash
cd ~/app
cp .env.example .env
```

Preencha os campos:
- `SHARED_SECRET`, `WEBHOOK_TOKEN_OPTIONAL`: segredos para webhooks.
- Credenciais Whaticket (`WHATSAPP_*`, `WHATICKET_JWT_*`).
- `REDIS_URL` e `DATABASE_URL` com usuários/senhas fortes.
- Ajuste limites (`CONTEXT_*`, `LLM_*`) conforme SLA.

### Integração Cal.com

Adicione ao `.env` da Secretária:
```env
CAL_API_URL=https://agenda.osmardev.online/api
CAL_API_KEY=sk_live_xxxxx
```
Essas chaves serão usadas pelos endpoints de agenda inteligente.

### Provedores de LLM

Suporte a Gemini e OpenAI configurado pelo módulo `app/services/llm.py` (injeta prompts, circuito e métricas).【F:app/services/llm.py†L1-L120】 Preencha uma das combinações abaixo:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy...
```

ou

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Parâmetros opcionais: `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` para controlar criatividade e custo.

### Boas práticas para o `.env`

- Gere `JWT_SECRET` exclusivo por ambiente.
- Não versione o arquivo. Use `chmod 600 .env` e armazene em cofre de segredos.
- Configure `POSTGRES_PASSWORD`, `REDIS_PASSWORD` e demais segredos com gerador randômico.

## 5. Orquestração com Docker Compose

O arquivo `docker-compose.prod.yml` provê todos os serviços do stack: API Flask `app`, worker RQ, Redis, PostgreSQL, Prometheus, Grafana e Alertmanager.【F:docker-compose.prod.yml†L1-L96】【F:docker-compose.prod.yml†L97-L139】 Pontos-chave:
- `app` e `worker` usam a mesma imagem `secretaria-app:latest` derivada de `docker/Dockerfile`.
- Volumes separados para banco, Redis, métricas e logs.
- Rede `public` apenas para `app`/`prometheus`; rede `internal` isola dependências.
- Healthchecks e restart policy `unless-stopped` para auto-recuperação.

### Deploy automatizado

O Makefile inclui os alvos `init`, `run`, `worker` e `deploy` para facilitar automações.【F:Makefile†L1-L46】 Use `DEPLOY_ARGS` para repassar flags ao script `scripts/deploy.sh`, que builda imagem, sobe os serviços e roda migrações.【F:scripts/deploy.sh†L1-L166】

Exemplo:
```bash
make deploy DEPLOY_ARGS="--domain osmardev.online --tenant-id 42 --tenant-slug acme"
```

Para acompanhar logs do container principal:
```bash
docker compose -f docker-compose.prod.yml logs -f app
```

## 6. Proxy reverso (Nginx ou Traefik)

### Nginx

Arquivo `/etc/nginx/sites-available/secretaria.conf`:
```nginx
server {
    listen 80;
    server_name api.osmardev.online;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Em seguida, use Certbot para habilitar TLS Let's Encrypt:
```bash
sudo certbot --nginx -d api.osmardev.online
```

### Traefik (dockerizado)

Adicione labels no serviço `app` quando utilizar Traefik:
```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.secretaria.rule=Host(`api.osmardev.online`)"
  - "traefik.http.routers.secretaria.entrypoints=websecure"
  - "traefik.http.routers.secretaria.tls.certresolver=letsencrypt"
```

## 7. Configuração de serviços externos

### Whaticket

1. Gere token JWT admin no painel Whaticket e armazene em `WHATSAPP_BEARER_TOKEN`.
2. Configure a URL da API (por exemplo `https://api.osmardev.online`) nas integrações.
3. Teste via Postman/HTTPie:
   ```bash
   curl -X POST https://api.osmardev.online/api/messages/send \
     -H "Authorization: Bearer $WHATSAPP_BEARER_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"number":"55999999999","body":"Teste integração Secretária"}'
   ```

### Cal.com

1. Instale a stack oficial do Cal.com em `agenda.osmardev.online` usando Docker.
2. Crie API Key de serviço e configure `CAL_API_URL`/`CAL_API_KEY` no `.env` conforme mostrado.
3. Valide disponibilidade:
   ```bash
   curl -H "X-Company-Domain: acme.com" "https://api.osmardev.online/api/agenda/availability?date=2024-07-01"
   ```

### Provedores LLM

- Defina `LLM_PROVIDER` + credenciais conforme seção 4.
- Ajuste parâmetros avançados (modelo, temperatura, tokens) para equilibrar qualidade/custo.
- Métricas de latência, erros e consumo são registradas em `/metrics` via `app/metrics.py`.【F:app/metrics.py†L1-L120】

### Prometheus & Grafana

- Prometheus lê `http://app:8080/metrics` e endpoints de infraestrutura definidos em `docker/prometheus/prometheus.yml`.
- Grafana é provisionado com dashboards `agenda_overview.json` e `business_ai.json` em `docker/grafana/dashboards/`.
- Acesse Grafana via túnel/Ingress (porta 3000) e configure datasources conforme provisionamento.

## 8. Procedimento de inicialização (desenvolvimento/homologação)

1. Preparar ambiente virtual e `.env`:
   ```bash
   make init
   source .venv/bin/activate
   ```
2. Aplicar migrações:
   ```bash
   make migrate
   ```
3. Iniciar worker local (em shell separado):
   ```bash
   make worker
   ```
4. Subir API:
   ```bash
   make run
   ```

O script `run.py` inicia o Flask na porta 8080.【F:run.py†L1-L9】 Valide endpoints principais:
- `GET /healthz` — verifica Postgres, Redis e heartbeat do worker.【F:app/routes/health.py†L1-L88】
- `GET /metrics` — expõe métricas Prometheus descritas em `app/metrics.py`.
- `/painel` — painel web (autenticação multi-tenant).

### Troubleshooting inicial

- `docker compose -f docker-compose.prod.yml ps` para checar status.
- `docker compose -f docker-compose.prod.yml logs -f worker` para investigar jobs.
- Use `rq info` (via `docker compose exec redis rq info`) para verificar filas.

## 9. Fluxo de uso e testes manuais

1. **Webhook Whaticket**: envie requisição teste para `POST /webhook/whaticket` usando `scripts/load_test_webhook.py` ou Postman com payload realista. Verifique contadores `secretaria_webhook_received_total` em `/metrics`.
2. **Agendamento pelo WhatsApp**: simule conversa via Whaticket; confirme que a IA gera horários sugeridos e cria `Appointment` no painel.
3. **Agenda inteligente / Cal.com**: acesse `/api/agenda/availability` e confirme que slots retornados refletem eventos do Cal.com.
4. **Painel multi-tenant**: autentique-se, visualize agendamentos, insights e follow-ups recém-gerados (versão 2.4 inclui painel Pós-Atendimento conforme release).
5. **Follow-up automático**: após um agendamento concluído, monitore fila RQ e confirme envio dos follow-ups (métricas `appointment_followups_*`).【F:docs/release_v2.4.md†L1-L83】
6. **Reiniciar worker**: `docker compose -f docker-compose.prod.yml restart worker`.
7. **Limpar fila**: `docker compose -f docker-compose.prod.yml exec redis redis-cli -n 0 FLUSHDB` (somente em homologação) ou utilize `rq` CLI para remover jobs específicos.

## 10. Observabilidade

- `GET /metrics` fornece counters de webhook, Whaticket, LLM, agenda e follow-up.【F:app/metrics.py†L1-L120】
- Prometheus coleta métricas e aciona alertas definidos em `docker/prometheus/alert.rules.yml`.
- Grafana dashboards padrão: `agenda_overview.json` (volume de agendamentos, taxa de comparecimento) e `business_ai.json` (tokens, intenções, saúde do LLM).

## 11. Segurança e boas práticas

- **Segredos**: mantenha `.env` fora do Git; utilize cofre (Vault, SSM) e rotação periódica (`scripts/rotate_secrets.py`).
- **Credenciais Redis/PostgreSQL**: ajuste `POSTGRES_USER`, `POSTGRES_PASSWORD` e configure `REDIS_URL` com senha (ex.: `redis://:senha@redis:6379/0`).
- **JWT**: defina `JWT_SECRET` único e forte para assinar tokens.
- **TLS**: obrigue HTTPS via Let's Encrypt (renovação automática `certbot renew`).
- **Backups**: agende `scripts/backup.sh` para rodar diariamente com `DATABASE_URL` apontando para o PostgreSQL.【F:scripts/backup.sh†L1-L25】 Use `scripts/restore.sh` para recuperar snapshots quando necessário.【F:scripts/restore.sh†L1-L27】 Armazene backups fora do servidor principal.
- **Rotação de tokens**: reprovisione chaves LLM e Whaticket trimestralmente ou após incidentes.
- **Monitoring hooks**: configure `MONITORING_WEBHOOK_URL` no deploy para receber alertas em caso de falhas.【F:scripts/deploy.sh†L167-L182】

## 12. Checklist final para produção

1. DNS apontando `api.osmardev.online` para o IP da VPS.
2. TLS válido emitido e renovação automática configurada.
3. `make deploy` executado com sucesso; containers `app`, `worker`, `postgres`, `redis`, `prometheus`, `grafana` saudáveis.
4. Migrações aplicadas (`alembic upgrade head` concluído sem erros via deploy script).【F:scripts/deploy.sh†L128-L148】
5. `GET /healthz` retornando `status=ok` e latências aceitáveis.【F:app/routes/health.py†L1-L88】
6. Métricas disponíveis em Prometheus e dashboards carregados no Grafana.
7. Webhook Whaticket testado e mensagem enviada com sucesso.
8. Integração Cal.com retornando disponibilidade correta.
9. Follow-ups automatizados disparando conforme plano (monitorar métricas).
10. Backups e restaurações testados (rodar `scripts/backup.sh` e `scripts/restore.sh`).
11. Documentar contatos de suporte e escalonamento em caso de incidentes.

Seguindo este guia, sua Secretária Virtual v2.4 estará pronta para operar com IA conversacional, agenda inteligente, follow-ups automatizados, métricas avançadas e painel multi-tenant em ambiente de produção confiável.
