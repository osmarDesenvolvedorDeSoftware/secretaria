# Release v2.4 – Follow-up Automático Pós-Atendimento

A versão 2.4 consolida o ciclo de atendimento após a reunião, adicionando follow-ups automatizados via WhatsApp, captação de feedback estruturado e reengajamento direto pela Agenda Inteligente.

## Visão geral

- Serviço dedicado `followup_service` que agenda jobs RQ uma hora após o término do compromisso (respeitando `allow_followup`).
- Mensagens automáticas com botões "✅ Sim, quero marcar" e "❌ Não, obrigado" para facilitar respostas rápidas do cliente.
- Interpretação das respostas pela `ContextEngine`, disparando novos fluxos de agendamento (`followup_positive`), encerrando o ciclo (`followup_negative`) ou registrando comentários livres (`followup_feedback`).
- Painel "Pós-Atendimento" na aba Agenda com taxa de resposta, filtros de status, reenviar follow-up e gráfico de satisfação de 30 dias.
- Auditoria completa (`followup_sent`, `followup_response`) e três novas métricas Prometheus focadas em engajamento pós-reunião.

## Fluxo do follow-up

1. **Agendamento:** ao criar ou sincronizar um `Appointment`, o `followup_service.agendar_followup` registra um job no RQ para `end_time + 1h`, salvo em `followup_next_scheduled`.
2. **Envio:** o job executa `followup_service.enviar_followup`, que envia a mensagem padrão via Whaticket e marca `followup_sent_at`.
3. **Resposta:**
   - **Positiva** (`sim, quero marcar`, `vamos agendar`…): intenção `followup_positive` inicia o fluxo `agenda.book`, apresenta horários sugeridos e marca `followup_response="positive"`.
   - **Negativa** (`não, obrigado`, `talvez depois`…): intenção `followup_negative` encerra o ciclo, registra auditoria e incrementa métricas negativas.
   - **Feedback textual:** intenção `followup_feedback` gera `FeedbackEvent` com `feedback_type="followup_text"` associado ao `appointment_id` e registra a resposta como `feedback`.
4. **Reengajamento manual:** o painel permite reenviar a mensagem para qualquer follow-up pendente/feedback direto do botão "Reenviar follow-up".

## Exemplos de mensagens WhatsApp

```
Espero que tenha corrido tudo bem na reunião de hoje, Ana. 😊
Gostaria de marcar o próximo encontro?

✅ Sim, quero marcar
❌ Não, obrigado
```

- **Resposta positiva:** "Sim, quero marcar" ⇒ bot responde `"Que ótimo! Veja abaixo algumas sugestões..."` seguido da lista de horários disponíveis.
- **Resposta negativa:** "Não, obrigado" ⇒ bot responde `"Sem problemas! Ficamos à disposição quando quiser retomar."`
- **Feedback livre:** "O atendimento foi excelente, só preciso de mais materiais." ⇒ bot responde `"Agradeço por compartilhar seu feedback!"` e registra o comentário.

## Configuração

1. Certifique-se de rodar as migrações (`alembic upgrade head`) para criar os campos:
   - `appointments.allow_followup`
   - `appointments.followup_sent_at`
   - `appointments.followup_response`
   - `appointments.followup_next_scheduled`
2. Garanta que novos agendamentos populam `allow_followup=True` quando houver consentimento LGPD.
3. Reinicie workers RQ para carregar o novo módulo `services/followup_service.py`.
4. Atualize dashboards Prometheus/Grafana com as métricas abaixo.

## Novas métricas Prometheus

| Métrica | Tipo | Descrição |
| --- | --- | --- |
| `secretaria_appointment_followups_sent_total{company}` | Counter | Total de mensagens de follow-up enviadas por empresa. |
| `secretaria_appointment_followups_positive_total{company}` | Counter | Respostas positivas registradas (`followup_positive`). |
| `secretaria_appointment_followups_negative_total{company}` | Counter | Respostas negativas registradas (`followup_negative`). |

Exemplo de painel:

```promql
sum(rate(secretaria_appointment_followups_sent_total[1d]))
```

```promql
sum by (company) (
  rate(secretaria_appointment_followups_positive_total[7d]) /
  clamp_min(rate(secretaria_appointment_followups_sent_total[7d]), 1)
)
```

## Painel "Pós-Atendimento"

- Taxa de resposta e contadores (👍 positivos, 👎 negativos, 💬 feedback, ⏳ pendentes) por filtro.
- Tabela com status por cliente, próxima tentativa (`followup_next_scheduled`) e botão "Reenviar follow-up".
- Gráfico empilhado (positivos x negativos) dos últimos 30 dias para acompanhamento rápido da satisfação pós-reunião.
- Endpoint de suporte: `GET /api/agenda/followups?company_id=...&status=all|positive|negative|pending` e `POST /api/agenda/followups/<id>/resend`.

## Auditoria e compliance

- Cada envio gera `AuditLog` com `action="followup_sent"`.
- Qualquer resposta (positiva, negativa ou feedback) gera `action="followup_response"` incluindo payload com `response` e `feedback`.
- Fluxo respeita `allow_followup=False`, ignorando agendamentos automáticos para clientes sem consentimento.

Com o follow-up automático, a Secretária Virtual fecha o ciclo de atendimento e mantém o cliente engajado para a próxima reunião sem intervenção humana.
