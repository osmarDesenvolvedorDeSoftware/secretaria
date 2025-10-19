# Release v2.1 – Agenda Inteligente

A versão 2.1 adiciona o módulo de Agenda Inteligente, integrando Cal.com ao fluxo multiempresa da Secretaria Virtual. A camada de backend ganhou serviços dedicados, métricas e webhook seguro; o painel recebeu uma nova aba com visão em tempo real dos compromissos e criação manual de reuniões.

## Configuração Cal.com

1. Gere um **API Key** no painel Cal.com e associe o valor ao tenant (coluna `companies.cal_api_key`).
2. Informe o **usuário padrão** responsável pelos slots (`companies.cal_default_user_id`). O ID será enviado nas chamadas de disponibilidade.
3. Cadastre um **segredo de webhook** (`companies.cal_webhook_secret`). Todas as notificações devem assinar o corpo cru com `hex(hmac_sha256(secret, body))` no header `X-Cal-Signature`.
4. Configure o endpoint do webhook para `POST /api/agenda/webhook/cal` incluindo `X-Cal-Company: <company_id>`.

> Dica: utilize a nova migração `0008_agenda_cal_integration.py` para aplicar os campos automaticamente em ambientes existentes.

## Fluxo WhatsApp → Cal.com

* Mensagens com intenção de agendamento (“quero marcar reunião”, “posso agendar um horário?”) são detectadas pelo `ContextEngine` e disparam o fluxo.
* A agenda consulta o Cal.com (`listar_disponibilidade`) e responde com as três próximas opções formatadas.
* Ao receber a confirmação (ex.: “1” ou “sim”), o worker cria o agendamento (`criar_agendamento`) e responde via Whaticket:

```
Reunião confirmada para amanhã às 14h! Aqui está o link: https://agenda.osmardev.online/meeting/abc123
```

* Todos os bookings ficam persistidos em `appointments` e auditados (`AuditLog`).

## Painel “Agenda”

O painel administrativo agora conta com uma aba dedicada:

* Filtro por data e busca por cliente.
* Tabela com status coloridos (confirmado, reagendado, cancelado) e ação de cancelamento.
* Botão “Criar manualmente” com formulário inline para disparar `POST /api/agenda/book`.
* Atualização em tempo real via AJAX e alerta contextual em caso de erro.

## Métricas Prometheus

Novos indicadores foram adicionados em `app/metrics.py`:

| Métrica | Descrição |
| --- | --- |
| `secretaria_appointments_total` | Tentativas de agendamento por tenant |
| `secretaria_appointments_confirmed_total` | Reuniões confirmadas |
| `secretaria_appointments_cancelled_total` | Cancelamentos via API/webhook |
| `secretaria_appointments_latency_seconds` | Latência das criações Cal.com |

Inclua-os no dashboard Grafana “Agenda Inteligente” para acompanhar taxa de conversão e tempo médio de resposta.

## Troubleshooting

| Sintoma | Ação sugerida |
| --- | --- |
| Webhook retornando 401 | Verifique `X-Cal-Company`, `X-Cal-Signature` e o segredo `cal_webhook_secret`. |
| Slots vazios no painel | Confirme `cal_default_user_id` e se o usuário possui disponibilidade em Cal.com. |
| Mensagem “agenda não configurada” no WhatsApp | Preencha `cal_api_key` para o tenant e reinicie o worker para invalidar caches. |
| Link ausente na confirmação | Garanta que o booking Cal.com retorne `meetingUrl` ou `url`; do contrário será exibido o fallback. |

## Referências rápidas

* Serviços: `app/services/cal_service.py`
* Blueprint API: `/api/agenda/*`
* Modelo: `app/models/appointment.py`
* Migração: `migrations/versions/0008_agenda_cal_integration.py`
