# Release v1.1 – Plataforma SaaS multiempresa

## Visão geral

A versão 1.1 converte a Secretária Virtual em uma plataforma SaaS multiempresa, com isolamento de dados, planos comerciais e monitoramento aprimorado por locatário. Abaixo estão as principais alterações e instruções para operar o novo modelo.

## Multi-tenancy completo

* **Novo modelo `Company`** com domínio exclusivo, status operacional e associação ao plano vigente.
* `Project`, `Conversation`, `CustomerContext`, `DeliveryLog` e `PersonalizationConfig` agora recebem `company_id` obrigatório.
* Filas RQ e chaves do Redis são separados por empresa (ex.: `company:42:ctx:...` e fila `default:company_42`).
* Tokens de painel carregam `company_id` e `scope`, permitindo autorização contextual.

## Planos e faturamento

* Modelos `Plan` e `Subscription` registram limites, preços e status de cobrança.
* Serviço `BillingService` centraliza atribuição de planos e processamento inicial de webhooks de pagamento.
* Consumo de mensagens e tokens é acumulado por empresa para apoiar billing e alertas.

## Painel administrativo

* Novo login requer seleção do `company_id`.
* Página `/painel/empresas` lista empresas, plano vigente, status de assinatura e consumo aproximado (mensagens e tokens).
* Endpoints REST permitem criação e edição de empresas, além de atribuição de planos.

## Observabilidade

* Métricas Prometheus agora incluem o label `company`. Exemplos:
  * `secretaria_webhook_received_total{company="1",status="accepted"}`
  * `secretaria_messages_processed_total{company="1",kind="assistant"}`
  * `secretaria_token_usage_total{company="1",direction="outbound"}`
* Alertas podem ser construídos por tenant usando os novos contadores.

## Migração

1. Execute `alembic upgrade head` para criar as tabelas `plans`, `companies`, `subscriptions` e adicionar `company_id` às tabelas existentes.
2. O script de migração cria automaticamente um plano "Starter" e a empresa padrão (`default.local`). Ajuste-os conforme necessário.
3. Atualize os serviços externos (webhook, painel, workers) para enviar `X-Company-Domain` ou tokens com `company_id` válido.

## Próximos passos sugeridos

* Integrar gateway de pagamento real (Stripe/Mercado Pago) nos webhooks do `BillingService`.
* Exibir gráfico de consumo mensal no painel.
* Criar limites automáticos de corte com base em `limite_mensagens` e `limite_tokens` do plano.
