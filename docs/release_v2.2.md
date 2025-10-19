# Release v2.2 – Agenda Proativa com Lembretes Inteligentes

## Visão geral

A versão 2.2 transforma a agenda em um assistente proativo. Lembretes automáticos (24h/1h) são enviados via WhatsApp com botões de confirmação e reagendamento. As respostas são interpretadas pelo worker, atualizando o status do compromisso, disparando métricas Prometheus e registrando auditoria. A agenda monitora no-shows automaticamente e o painel ganhou filtros dinâmicos, taxa de presença e ação manual de lembrete.

## Fluxo completo

1. **Criação do agendamento** – `cal_service.criar_agendamento` persiste `Appointment` com `status="pending"`, agenda lembretes (`reminder_service`) e marca a checagem de no-show (`no_show_service`).
2. **Lembrete proativo** – Jobs RQ executam `reminder_service.enviar_lembrete`, enviando mensagem com botões “Confirmar presença” e “Reagendar”, registrando `AuditLog` e a métrica `appointment_reminders_sent_total`.
3. **Confirmação pelo cliente** – Mensagens como “confirmar”, “ok” ou “estarei lá” atualizam o compromisso (`status="confirmed"`, `confirmed_at`), incrementam `appointment_confirmations_total` e registram `AuditLog`.
4. **Reagendamento inteligente** – Pedidos de “adiar” ou “remarcar” disparam nova consulta de disponibilidade Cal.com, criam um novo booking com `reschedule=True`, atualizam o antigo para `status="rescheduled"` e notificam o cliente.
5. **Detecção de no-show** – Após `end_time + 30min`, `no_show_service.verificar_no_show` marca compromissos não confirmados como `no_show`, adiciona feedback “Cliente não compareceu” e incrementa `appointment_no_show_total`.
6. **Painel operacional** – A aba Agenda exibe o status com ícones, taxa de presença (% confirmados/total), filtros rápidos (“Hoje”, “Próximos 7 dias”, “Não confirmados”) e botão “Enviar lembrete agora”. Tooltips mostram o histórico de lembretes enviados.

## Exemplos de mensagens WhatsApp

- **Lembrete automático**  
  `📅 Olá Ana, lembrando da sua reunião às 15h30. Deseja confirmar ou reagendar?

✅ Confirmar presença
🔄 Reagendar`

- **Confirmação registrada**  
  `Perfeito! Sua presença está confirmada para 05/08 às 15h30. Até lá!`

- **Reagendamento concluído**  
  `Tudo certo, reagendamos sua reunião para 06/08 às 10h00 ✅
Novo link: https://cal.com/...`

- **Mensagem de no-show (registro interno)**  
  `AuditLog: appointment.no_show_detected → Cliente não compareceu`

## Métricas e alertas Prometheus

| Métrica | Descrição |
| --- | --- |
| `secretaria_appointment_reminders_sent_total{type="24h|1h|manual"}` | Total de lembretes disparados por tipo. |
| `secretaria_appointment_confirmations_total{company="..."}` | Confirmações registradas via WhatsApp. |
| `secretaria_appointment_reschedules_total{company="..."}` | Reagendamentos concluídos com sucesso. |
| `secretaria_appointment_no_show_total{company="..."}` | No-shows detectados automaticamente. |
| `secretaria_appointments_total/confirmed/cancelled` | Métricas já existentes para volumetria geral. |

Alertas recomendados:

- **Reminders sem confirmação**: se `appointment_reminders_sent_total` cresce enquanto `appointment_confirmations_total` permanece constante por X horas.
- **No-show elevado**: gatilho quando `appointment_no_show_total` aumenta acima de limiar diário.
- **Reschedules em excesso**: alerta se `appointment_reschedules_total` cresce além do baseline semanal.

## Painel “Agenda Inteligente”

- Nova seção com **Taxa de presença** em destaque e filtros rápidos (Hoje, Próximos 7 dias, Não confirmados).
- Coluna de status com ícones/cores (⏳ Pendente, ✅ Confirmado, 🔁 Reagendado, ⚠️ No-show, ❌ Cancelado).
- Botão **“Enviar lembrete agora”** dispara `POST /api/agenda/appointments/<id>/reminder` e atualiza a tabela automaticamente.
- Tooltips exibem os horários dos últimos lembretes (24h / 1h) para cada compromisso.
- Confirmações, reagendamentos e no-shows alimentam o `AuditLog`, mantendo trilha de auditoria.

## Dependências e migração

- Migração `0009_appointments_reminders_reschedule.py` adiciona campos `confirmed_at`, `reminder_24h_sent`, `reminder_1h_sent`, `no_show_checked` e altera o default de `status` para `pending`.
- Novos serviços: `app/services/reminder_service.py` e `app/services/no_show_service.py`.
- Atualizações em `context_engine` e `TaskService` permitem interpretar intenções de confirmação/reagendamento.
- Métricas expostas em `app/metrics.py` e consumidas pelo painel via `/api/agenda/appointments` (inclui `attendance_rate`).
