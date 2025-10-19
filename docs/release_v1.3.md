# Secretaria Virtual v1.3 – Analytics e Faturamento em Tempo Real

## Visão Geral

A versão 1.3 transforma a Secretaria Virtual em um SaaS com visão financeira completa. As novidades incluem agregação periódica de métricas por empresa, cálculo de custo em tempo real e ferramentas para tomada de decisão rápida.

### Principais destaques

* **Serviço de analytics** com agregações diárias e semanais persistidas em `analytics_reports`.
* **Integração com BillingService** para atualizar uso, custo estimado e alertas de limite em tempo real.
* **Painel administrativo** com aba “Analytics e Consumo”, gráficos interativos e exportação de relatórios CSV/PDF.
* **Endpoints REST** dedicados (`/api/analytics/summary` e `/api/analytics/history`) protegidos por autenticação do painel.
* **Alertas automáticos** (webhook + painel) ao atingir 80% e 100% dos limites do plano.

## Monitoramento financeiro

1. Defina os custos por mensagem e por mil tokens via variáveis de ambiente `BILLING_COST_PER_MESSAGE` e `BILLING_COST_PER_THOUSAND_TOKENS`.
2. O `AnalyticsService` registra automaticamente mensagens/tokens inbound e outbound, tempo médio de resposta e custo incremental.
3. Os relatórios diários e semanais ficam disponíveis na tabela `analytics_reports`, permitindo auditoria histórica.

## Billing em tempo real

1. O `BillingService.record_usage()` delega o cálculo de custo e o armazenamento para o `AnalyticsService`.
2. Alertas são gravados no Redis e enviados para o webhook configurado em `BILLING_ALERT_WEBHOOK_URL`.
3. O painel exibe o progresso de consumo do plano e destaca quando limites são alcançados.

## Painel “Analytics e Consumo”

* Resumo de consumo (mensagens, tokens, SLA médio e custo estimado) por empresa.
* Gráficos de tendência diários/semanais com Chart.js (mensagens, tokens, tempo e custo).
* Lista de alertas recentes, diferenciando avisos (80%) e criticidade (100%).
* Botões de exportação que geram relatórios CSV ou PDF sem sair do painel.

## API de relatórios

```
GET /api/analytics/summary?company_id=<id>
GET /api/analytics/history?company_id=<id>&period=week|month
GET /api/analytics/export?company_id=<id>&format=csv|pdf
```

* Requer token do painel (`Bearer <token>` ou cookie `panel_token`).
* `summary` traz uso atual, agregados diário/semanal e alertas.
* `history` retorna séries temporais (7 dias ou 8 semanas).
* `export` entrega o relatório pronto para download.

## CLI – Exportação de relatórios

Execute via Makefile:

```bash
make report COMPANY_ID=1 FORMAT=pdf
```

O comando utiliza o mesmo serviço interno do painel para gerar CSV ou PDF em `./reports/`.

## Próximos passos sugeridos

* Suporte a planos com múltiplos centros de custo.
* Dashboard comparativo entre empresas (benchmarking).
* Integração direta com provedores de pagamento para suspender/reativar tenants automaticamente.
