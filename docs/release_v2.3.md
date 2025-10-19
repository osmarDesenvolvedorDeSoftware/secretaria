# Release v2.3 – IA de Otimização de Agenda

## Visão geral

A versão 2.3 adiciona uma camada de inteligência preditiva sobre a agenda Cal.com. A plataforma agora aprende padrões de presença, antecipa faltas prováveis e sugere reagendamentos de alta conversão diretamente pelo WhatsApp.

Principais entregas:

- 📈 **IA de otimização de horários** com análise histórica (180 dias), regressão heurística por dia/horário e armazenamento dos insights na nova tabela `scheduling_insights`.
- 🤖 **Serviço de reagendamento automático** que marca compromissos de alto risco, consulta slots sugeridos e envia convite proativo ao cliente.
- 📊 **Painel “Insights de Agenda”** exibindo recomendação textual, gráfico dos horários mais confiáveis e botão “Reagendar automaticamente faltas”.
- 🔭 **Métricas Prometheus e dashboard Grafana “Agenda IA”** com heatmap de presença otimizada e contadores de risco/reagendamento.
- 🛠️ **Scheduler RQ diário** (via `SchedulerService`) acionando `scheduling_ai.analisar_padroes` para todas as empresas.

## Como a IA aprende padrões

1. `scheduling_ai.analisar_padroes(company_id)` percorre compromissos dos últimos 180 dias (`Appointment`) e agrega indicadores por dia da semana e hora.
2. Cada slot gera métricas de presença (`attendance_rate`), ausência (`no_show_prob`), volume e confirmações. O score final prioriza janelas estáveis, com alta confirmação e baixa taxa de falta.
3. Os resultados são persistidos em `scheduling_insights` (colunas `weekday`, `hour`, `attendance_rate`, `no_show_prob`, `suggested_slot`, `updated_at`). Os três melhores horários por empresa são marcados como `suggested_slot=True`.
4. `scheduling_ai.prever_no_show(appointment)` combina o histórico do slot, status atual, lembretes enviados e heurísticas (manhãs, segundas/sextas, ausência de confirmação) para calcular a probabilidade de falta.
5. `scheduling_ai.sugerir_horarios_otimizados(company_id)` expõe a lista priorizada de janelas com label amigável (“Segunda · 14h-15h”) para o painel e automações.

## Atualização diária via RQ Scheduler

- O `SchedulerService` (novo serviço inicializado em `app/__init__.py`) agenda diariamente o job `scheduling_ai.atualizar_insights_job` para cada empresa.
- A execução é idempotente: o serviço guarda o `last_run` no Redis e só agenda novamente após 23 horas, evitando enfileirar jobs duplicados.
- O job roda no worker RQ padrão (`app/workers/rq_worker.py`). Em ambientes com vários tenants recomenda-se um worker dedicado para a fila de otimização.
- Para forçar a atualização manual em um shell Flask:
  ```python
  from app.services import scheduling_ai
  scheduling_ai.analisar_padroes(company_id=1)
  ```
- Métrica: `secretaria_agenda_optimization_runs_total{company="..."}` incrementa a cada execução concluída por empresa.

## Reagendamento Automático Inteligente

Fluxo principal (`auto_reschedule_service.executar_reagendamento`):

1. Carrega compromissos das próximas horas e calcula o risco (`prever_no_show`). Casos acima do limiar (padrão 0.8) ou sem resposta ao lembrete 24h são marcados como “alto risco”.
2. Para cada cliente crítico, consulta `scheduling_ai.sugerir_horarios_otimizados` e checa a disponibilidade Cal.com, priorizando o slot recomendado.
3. Envia mensagem proativa no WhatsApp via `WhaticketClient` (ex.: “Percebi que o horário das 8h costuma ter mais imprevistos. Que tal reagendarmos para 14h, onde há menos cancelamentos?”).
4. Atualiza o estado da agenda no `ContextEngine` com opções já ordenadas, permitindo que a assistente confirme a opção “1” de imediato.
5. Métricas incrementadas:
   - `secretaria_appointments_risk_high_total{company="..."}` – compromissos sinalizados como risco.
   - `secretaria_appointments_auto_rescheduled_total{company="..."}` – mensagens de reagendamento automático enviadas.

O mesmo mecanismo é acionado dentro do fluxo WhatsApp (`_handle_agenda_flow`): se o cliente interage e o horário atual tem probabilidade >0.8, a assistente oferece automaticamente novos slots antes da confirmação manual.

## Painel “Insights de Agenda”

- Card dedicado com recomendação textual (“Segundas entre 14h–17h têm 30% menos faltas”), data/hora da última análise e botão “Reagendar automaticamente faltas”.
- Gráfico Chart.js apresenta as janelas sugeridas com a taxa de presença estimada. Tooltip mostra o percentual de no-show para cada slot.
- Ao acionar o botão, o painel chama `/api/agenda/auto-reschedule` e exibe feedback sobre clientes contatados.
- Captura de tela: adicione manualmente `docs/images/v2.3-agenda-insights.png` antes de publicar o release (arquivo omitido neste commit).

## Métricas e observabilidade

| Métrica Prometheus | Descrição |
| --- | --- |
| `secretaria_appointments_risk_high_total{company=...}` | Total de compromissos marcados como alto risco de no-show. |
| `secretaria_appointments_auto_rescheduled_total{company=...}` | Reagendamentos automáticos disparados pela IA. |
| `secretaria_agenda_optimization_runs_total{company=...}` | Execuções da análise de padrões por empresa. |

Dashboard Grafana “Agenda IA” (novo):

- Heatmap com presença média por dia/hora usando os dados de `scheduling_insights` (exportados para Prometheus).
- Série temporal dos contadores de risco/reagendamento para avaliar efetividade das ações.
- Alertas recomendados: disparar notificação se `appointments_risk_high_total` crescer sem acompanhamento de `auto_rescheduled_total`.

## Configuração do job diário

1. Garanta que o worker RQ esteja ativo (`python -m app.workers.rq_worker`).
2. Ao subir o Flask (`init_app`), o `SchedulerService` agenda automaticamente as execuções. Em ambientes com vários pods, mantenha apenas uma instância executando a rotina (utilize lock externo ou configure o serviço apenas no pod primário).
3. Para ambientes legados, é possível acionar manualmente:
   ```python
   from app.services.scheduler_service import SchedulerService
   scheduler = SchedulerService(app.redis, app.db_session, app.get_task_queue)
   scheduler.ensure_daily_agenda_optimization(force=True)
   ```
4. Verifique a fila (`rq info agenda:company_<id>`) para confirmar o enfileiramento dos jobs.

## Requisitos de hardware mínimos

- **Aplicação Flask**: 2 vCPUs, 2 GB RAM.
- **Worker RQ** dedicado à IA de agenda: 1 vCPU, 1 GB RAM (processa análise diária em menos de 2s para ~5k compromissos).
- **Redis**: manter 512 MB livres para armazenar estados de agenda + cache de insights.
- **PostgreSQL**: crescimento estimado < 5 MB/mês para `scheduling_insights` (1 linha por hora/dia/empresa).

## Testes automatizados

- `tests/test_scheduling_ai.py` – valida análise histórica, previsão de no-show e ordenação das sugestões.
- `tests/test_auto_reschedule_service.py` – garante que compromissos de risco geram mensagem automática, atualizam métricas e contexto.
- `tests/test_optimization_metrics.py` – cobre exposição das novas métricas em `/metrics`.
- Atualizações adicionais nos testes de API (`test_agenda_api.py`) confirmam os novos endpoints `/api/agenda/insights` e `/api/agenda/auto-reschedule`.

## Migração de banco

- Alembic `0010_scheduling_ai_optimization.py` cria a tabela `scheduling_insights` com índice por empresa e chave única (`company_id`, `weekday`, `hour`).

Com essas melhorias, a Agenda Inteligente passa a atuar de forma proativa: detecta riscos, sugere automaticamente slots com maior taxa de comparecimento e oferece reagendamento assistido, elevando a taxa de presença em atendimentos recorrentes.
