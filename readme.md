# Secretária Osmar

Aplicação Flask com painel administrativo e API REST para auxiliar o desenvolvedor Osmar no gerenciamento de projetos e reuniões.

## Requisitos
- Python 3.11+
- Dependências listadas em `requirements.txt` (ver abaixo)

## Instalação
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuração de ambiente
1. Duplique o arquivo `.env.example` para `.env` e ajuste os valores:
   - `FLASK_SECRET_KEY`, credenciais de login (`ADMIN_USERNAME`/`ADMIN_PASSWORD`),
   - `GEMINI_API_KEY` e demais opções do Gemini,
   - `WHATICKET_WEBHOOK_TOKEN` para validar chamadas recebidas,
   - `WHATSAPP_BEARER_TOKEN` e URL/timeout do disparador de mensagens.
2. (Opcional) Ajuste `SQLALCHEMY_DATABASE_URI` se desejar outro banco.

## Execução
```bash
python app.py
```
A aplicação será exposta em `http://127.0.0.1:5000/`.

## Credenciais padrão
- Usuário: `osmar`
- Senha: `123456`

> Todos os valores podem (e devem) ser alterados no `.env` em produção.

## Fluxo do Chatbot
O arquivo `app.py` contém a constante `CHATBOT_WORKFLOW` descrevendo como o chatbot deve consumir a API:
1. Cumprimentar o cliente e identificar a necessidade.
2. Consultar `GET /api/projects` para listar os projetos.
3. Agendar uma reunião via `POST /api/meetings`.
4. Após a confirmação manual da reunião no painel, chamar `notify_osmar_via_callmebot` para avisar o Osmar via WhatsApp.

## Dependências sugeridas (`requirements.txt`)
```
Flask==3.0.2
Flask-Login==0.6.3
Flask-SQLAlchemy==3.1.1
requests==2.31.0
python-dotenv==1.0.1
```

> Caso prefira, instale manualmente com `pip install -r requirements.txt`.

## Webhook Whaticket
- Endpoint: `POST /webhook/whaticket`
- Headers opcionais: `X-Webhook-Token` (se `WHATICKET_WEBHOOK_TOKEN` estiver configurado).
- Payload mínimo esperado: `{ "number": "55999999999", "body": "Mensagem recebida" }`.
- Fluxo:
  1. A mensagem recebida é enviada ao Gemini (modelo configurado em `GEMINI_MODEL`).
  2. A resposta gerada é reenviada ao contato utilizando a API definida em `WHATSAPP_API_URL`.
  3. Em caso de falha na IA ou no disparo, o endpoint retorna um erro JSON específico (`falha_ia` ou `falha_envio`).
