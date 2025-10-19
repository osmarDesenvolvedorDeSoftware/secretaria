# Release v1.2 — Auto-provisionamento multi-tenant

A versão 1.2 da Secretária Virtual entrega a primeira iteração de **auto-provisionamento completo** para novos tenants. A plataforma agora consegue criar empresas isoladas ponta-a-ponta, com banco dedicado, Redis segregado, filas RQ específicas e observabilidade por tenant.

## Principais destaques

- Endpoint administrativo `/api/tenants/provision` capaz de criar empresa, plano e assinatura automaticamente e registrar toda a infraestrutura necessária.
- Registro dos tenants em schemas dedicados no PostgreSQL e conexão automática com Redis isolado (`redis://…/tenant_{id}`).
- Script `scripts/spawn_worker.py` para iniciar workers por tenant com registro de estado no Redis e observabilidade no `/metrics`.
- `scripts/deploy.sh` ampliado para provisionar subdomínios `chat.<tenant>.<domínio>` e `api.<tenant>.<domínio>` com status de SSL armazenado para o painel.
- Painel administrativo atualizado com fluxo “Nova Empresa”, acompanhamento do provisionamento e exibição do status de domínio/SSL e workers.
- Documentação de rollback e operação multi-tenant aprimorada.

## Provisionamento de tenants

1. Autentique-se no painel e acesse a seção **Nova Empresa**.
2. Informe nome, domínio principal, plano e (opcionalmente) domínio base da plataforma e slug do tenant.
3. Após o envio do formulário, o backend irá:
   - Criar registros em `plans`, `companies` e `subscriptions`;
   - Criar schema dedicado `tenant_{company_id}` no banco PostgreSQL;
   - Registrar Redis isolado (`redis://…/tenant_{company_id}`) e fila RQ (`default:company_{id}`);
   - Persistir metadados de provisionamento, domínios e infraestrutura no Redis para auditoria.
4. O endpoint retorna token inicial de acesso ao painel, instruções de worker (`python scripts/spawn_worker.py --company-id <id>`) e metadados de domínio.
5. Um log `provisioning.email` é gravado contendo credenciais iniciais simulando o envio de e-mail.

### Rollback do provisionamento

Em caso de erro durante o provisionamento:

1. Remova o tenant do Redis (`DEL tenant:{id}:*`).
2. Drope o schema PostgreSQL `DROP SCHEMA IF EXISTS "tenant_{id}" CASCADE;`.
3. Apague registros nas tabelas `subscriptions`, `companies` e `plans` correspondentes.
4. Revogue o token inicial emitido removendo-o dos canais de distribuição (log/sistema de tickets).

## Filas e workers por tenant

- O script `scripts/spawn_worker.py` aceita `--company-id`, `--queue`, `--burst` e `--worker-id`.
- Cada execução registra o worker em `tenant:{id}:workers` e metadados em `tenant:{id}:worker:<uuid>`.
- O `/metrics` passa a expor `secretaria_tenant_active_workers{company="<id>"}` com o número de workers ativos por tenant.
- Durante o desligamento (SIGTERM/SIGINT ou término natural) o script atualiza o status e limpa o registro no Redis.

## Domínios e SSL

- `scripts/deploy.sh` agora aceita `--domain`, `--tenant-id` e `--tenant-slug` (opcional).
- Ao informar domínio e tenant, o script registra os subdomínios `chat.<tenant>.<domínio>` e `api.<tenant>.<domínio>` e marca `domain_status`/`ssl_status` como `ready`/`active` no Redis.
- Logs de provisionamento de domínio são gravados em `deployments/domain_status.log`.

## Painel e onboarding

- Nova seção **Nova Empresa** permite acompanhar o progresso do provisionamento (Banco, Fila/Redis, Domínios, Worker) e visualizar o token inicial.
- A tabela de empresas exibe status de domínio, SSL, workers ativos e detalhes de infraestrutura (schema, fila, Redis).
- A UI sugere automaticamente o slug do tenant a partir do nome da empresa.

## Próximos passos sugeridos

- Automatizar disparo do `spawn_worker.py` pós-provisionamento (hooks ou orchestrator).
- Integrar com provedor real de DNS/SSL (Cloudflare API, certbot ACME) substituindo o mock atual.
- Criar auditoria/telemetria para acompanhar falhas de provisionamento e permitir reprocessamento.
- Implementar exclusão controlada de tenants com limpeza automatizada de recursos.
