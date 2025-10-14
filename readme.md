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

## Execução
```bash
python app.py
```
A aplicação será exposta em `http://127.0.0.1:5000/`.

## Credenciais padrão
- Usuário: `osmar`
- Senha: `123456`

> Altere esses valores no arquivo `app.py` em `app.config` para produção e use variáveis de ambiente.

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
```

> Caso prefira, instale manualmente com `pip install Flask Flask-Login Flask-SQLAlchemy requests`.
