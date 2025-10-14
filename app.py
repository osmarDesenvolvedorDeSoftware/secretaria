"""Aplicação Flask "Secretária Osmar".

Este módulo reúne toda a lógica da aplicação web solicitada:
- Configuração do Flask e da extensão SQLAlchemy;
- Modelos `Project` e `Meeting` com serialização para JSON;
- Rotas HTML (com Bootstrap) para gerenciamento de projetos e reuniões;
- API RESTful simples para consulta e criação de registros;
- Autenticação mínima com o usuário "osmar"/"123456";
- Função `notify_osmar_via_callmebot` simulada, apenas imprimindo no console.

Execute com `python app.py` e acesse http://localhost:5000.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
import requests
from dotenv import load_dotenv

# ----------------------------------------------------------------------------
# Configuração da aplicação e extensões
# ----------------------------------------------------------------------------
load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", "segredo-super-seguro"),
    SQLALCHEMY_DATABASE_URI=os.environ.get(
        "SQLALCHEMY_DATABASE_URI", "sqlite:///secretaria_osmar.db"
    ),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    ADMIN_USERNAME=os.environ.get("ADMIN_USERNAME", "osmar"),
    ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", "123456"),
    GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY"),
    GEMINI_MODEL=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    GEMINI_TIMEOUT=int(os.environ.get("GEMINI_TIMEOUT", 15)),
    WHATICKET_WEBHOOK_TOKEN=os.environ.get("WHATICKET_WEBHOOK_TOKEN"),
    WHATSAPP_API_URL=os.environ.get(
        "WHATSAPP_API_URL", "https://api.osmardev.online/api/messages/send"
    ),
    WHATSAPP_BEARER_TOKEN=os.environ.get("WHATSAPP_BEARER_TOKEN"),
    WHATSAPP_TIMEOUT=int(os.environ.get("WHATSAPP_TIMEOUT", 15)),
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


# ----------------------------------------------------------------------------
# Modelos de dados
# ----------------------------------------------------------------------------
class Project(db.Model):
    """Projetos apresentados pelo Osmar."""

    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    tech_stack = db.Column(db.Text)
    demo_url = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tech_stack": self.tech_stack,
            "demo_url": self.demo_url,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Meeting(db.Model):
    """Reuniões agendadas pelo chatbot."""

    __tablename__ = "meetings"

    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(255), nullable=False)
    contact = db.Column(db.String(255))
    datetime = db.Column(db.DateTime)
    status = db.Column(db.String(50), nullable=False, default="pending")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "client_name": self.client_name,
            "contact": self.contact,
            "datetime": self.datetime.isoformat() if self.datetime else None,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
        }


# ----------------------------------------------------------------------------
# Autenticação simplificada (usuário único)
# ----------------------------------------------------------------------------
class AdminUser(UserMixin):
    def __init__(self, user_id: str, username: str) -> None:
        self.id = user_id
        self.username = username


@login_manager.user_loader
def load_user(user_id: str) -> Optional[AdminUser]:
    if user_id == "admin":
        return AdminUser("admin", app.config["ADMIN_USERNAME"])
    return None


# ----------------------------------------------------------------------------
# Funções utilitárias
# ----------------------------------------------------------------------------
def notify_osmar_via_callmebot(message: str) -> None:
    """Simula uma notificação via CallMeBot apenas imprimindo no console."""

    print(f"[CallMeBot] {message}")


def generate_gemini_response(prompt: str) -> str:
    """Solicita uma resposta ao modelo Gemini configurado na aplicação."""

    api_key = app.config.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY não configurada.")

    model = app.config.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt,
                    }
                ]
            }
        ]
    }
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=app.config.get("GEMINI_TIMEOUT", 15),
    )
    response.raise_for_status()
    data = response.json()

    candidates = data.get("candidates", [])
    for candidate in candidates:
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                return text.strip()

    raise RuntimeError("Resposta inválida do Gemini.")


def send_whatsapp_message(number: str, body: str) -> None:
    """Envia uma mensagem via API configurada para disparos WhatsApp/Whaticket."""

    token = app.config.get("WHATSAPP_BEARER_TOKEN")
    if not token:
        raise RuntimeError("WHATSAPP_BEARER_TOKEN não configurado.")

    url = app.config.get("WHATSAPP_API_URL")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"number": number, "body": body}

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=app.config.get("WHATSAPP_TIMEOUT", 15),
    )
    response.raise_for_status()


@app.route("/webhook/whaticket", methods=["POST"])
def whaticket_webhook():
    """Recebe eventos do Whaticket, gera resposta via Gemini e envia pelo disparador."""

    expected_token = app.config.get("WHATICKET_WEBHOOK_TOKEN")
    if expected_token:
        provided_token = request.headers.get("X-Webhook-Token") or request.args.get("token")
        if provided_token != expected_token:
            app.logger.warning("Webhook recebido com token inválido.")
            return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    message_text = (
        payload.get("body")
        or payload.get("message")
        or payload.get("text")
        or payload.get("content")
    )
    number = payload.get("number") or payload.get("from") or payload.get("contact")

    if not message_text or not number:
        return (
            jsonify({"error": "Campos 'number' e 'body' são obrigatórios no payload."}),
            400,
        )

    try:
        reply_text = generate_gemini_response(message_text)
    except Exception as exc:  # pragma: no cover - log para depuração
        app.logger.exception("Falha ao gerar resposta com Gemini: %s", exc)
        return jsonify({"error": "falha_ia"}), 500

    try:
        send_whatsapp_message(number, reply_text)
    except requests.RequestException as exc:  # pragma: no cover - log para depuração
        app.logger.exception("Falha ao enviar mensagem para %s: %s", number, exc)
        return jsonify({"error": "falha_envio"}), 502
    except Exception as exc:  # pragma: no cover - log para depuração
        app.logger.exception("Erro inesperado ao enviar mensagem: %s", exc)
        return jsonify({"error": "falha_envio"}), 500

    return jsonify({"status": "ok", "reply": reply_text})


@app.before_first_request
def create_database_and_seed() -> None:
    """Cria as tabelas e adiciona registros de exemplo se o banco estiver vazio."""

    db.create_all()

    if Project.query.count() == 0:
        sample_projects = [
            Project(
                name="Aplicativo QR Code",
                description="App Android para leitura de QR Code com Kotlin e Firebase.",
                tech_stack="Kotlin, MVVM, Firebase",
                demo_url="https://github.com/osmar/android-qrcode-demo",
            ),
            Project(
                name="Dashboard de Vendas",
                description="Dashboard web integrando Shopee, Google Sheets e WhatsApp.",
                tech_stack="Python, Flask, Bootstrap",
                demo_url="https://github.com/osmar/dashboard-demo",
            ),
        ]
        db.session.add_all(sample_projects)
        db.session.commit()

    if Meeting.query.count() == 0:
        meeting = Meeting(
            client_name="Cliente Exemplo",
            contact="cliente@example.com",
            datetime=datetime.utcnow(),
            notes="Reunião inicial demonstrativa.",
            status="pending",
        )
        db.session.add(meeting)
        db.session.commit()


# ----------------------------------------------------------------------------
# Rotas HTML protegidas por login
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if (
            username == app.config["ADMIN_USERNAME"]
            and password == app.config["ADMIN_PASSWORD"]
        ):
            login_user(AdminUser("admin", username))
            flash("Login realizado com sucesso!", "success")
            return redirect(url_for("dashboard"))

        flash("Credenciais inválidas.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sessão encerrada.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    projects_count = Project.query.count()
    meetings_pending = Meeting.query.filter_by(status="pending").count()
    meetings_confirmed = Meeting.query.filter_by(status="confirmed").count()
    return render_template(
        "dashboard.html",
        projects_count=projects_count,
        meetings_pending=meetings_pending,
        meetings_confirmed=meetings_confirmed,
    )


@app.route("/admin/projects", methods=["GET", "POST"])
@login_required
def manage_projects():
    if request.method == "POST":
        project = Project(
            name=request.form.get("name", "").strip(),
            description=request.form.get("description"),
            tech_stack=request.form.get("tech_stack"),
            demo_url=request.form.get("demo_url"),
        )

        if not project.name:
            flash("O nome do projeto é obrigatório.", "danger")
        else:
            db.session.add(project)
            db.session.commit()
            flash("Projeto cadastrado com sucesso!", "success")
            return redirect(url_for("manage_projects"))

    projects = Project.query.order_by(Project.updated_at.desc()).all()
    return render_template("projects.html", projects=projects)


@app.route("/admin/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit_project(project_id: int):
    project = Project.query.get_or_404(project_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("O nome do projeto é obrigatório.", "danger")
        else:
            project.name = name
            project.description = request.form.get("description")
            project.tech_stack = request.form.get("tech_stack")
            project.demo_url = request.form.get("demo_url")
            project.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Projeto atualizado!", "success")
            return redirect(url_for("manage_projects"))

    return render_template("project_form.html", project=project)


@app.route("/admin/projects/<int:project_id>/delete", methods=["POST"])
@login_required
def delete_project(project_id: int):
    project = Project.query.get_or_404(project_id)
    db.session.delete(project)
    db.session.commit()
    flash("Projeto excluído.", "info")
    return redirect(url_for("manage_projects"))


@app.route("/admin/meetings", methods=["GET", "POST"])
@login_required
def manage_meetings():
    if request.method == "POST":
        meeting_id = request.form.get("meeting_id", type=int)
        action = request.form.get("action")
        meeting = Meeting.query.get_or_404(meeting_id)

        if action == "confirm":
            meeting.status = "confirmed"
            db.session.commit()
            flash("Reunião confirmada. Osmar foi notificado!", "success")
            notify_osmar_via_callmebot(
                f"Reunião confirmada com {meeting.client_name} em {meeting.datetime}."
            )
        elif action == "cancel":
            meeting.status = "cancelled"
            db.session.commit()
            flash("Reunião cancelada.", "warning")
        else:
            flash("Ação inválida.", "danger")

        return redirect(url_for("manage_meetings"))

    meetings = Meeting.query.order_by(Meeting.datetime.desc()).all()
    return render_template("meetings.html", meetings=meetings)


# ----------------------------------------------------------------------------
# API REST
# ----------------------------------------------------------------------------
@app.route("/api/projects", methods=["GET"])
def api_projects():
    projects = Project.query.order_by(Project.updated_at.desc()).all()
    return jsonify([project.to_dict() for project in projects])


@app.route("/api/meetings", methods=["GET", "POST"])
def api_meetings():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        client_name = (payload.get("client_name") or "").strip()
        contact = payload.get("contact")
        datetime_str = payload.get("datetime")
        notes = payload.get("notes")

        if not client_name:
            return jsonify({"status": "error", "message": "Nome do cliente é obrigatório."}), 400

        meeting_datetime: Optional[datetime] = None
        if datetime_str:
            try:
                meeting_datetime = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
            except ValueError:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Formato inválido. Use YYYY-MM-DD HH:MM.",
                        }
                    ),
                    400,
                )

        meeting = Meeting(
            client_name=client_name,
            contact=contact,
            datetime=meeting_datetime,
            notes=notes,
        )
        db.session.add(meeting)
        db.session.commit()
        return jsonify({"status": "success", "meeting": meeting.to_dict()}), 201

    meetings = Meeting.query.order_by(Meeting.datetime.desc()).all()
    return jsonify([meeting.to_dict() for meeting in meetings])


# ----------------------------------------------------------------------------
# Conteúdo auxiliar para os templates
# ----------------------------------------------------------------------------
CHATBOT_WORKFLOW = """
Fluxo sugerido para o chatbot Secretária Osmar:
1. Cumprimentar o cliente e identificar o objetivo do contato.
2. Consultar GET /api/projects para apresentar os principais trabalhos do Osmar.
3. Recolher nome, contato e data desejada para criar uma reunião via POST /api/meetings.
4. Após confirmação manual no painel (/admin/meetings), o Osmar é avisado pela função notify_osmar_via_callmebot.
"""


@app.context_processor
def inject_chatbot_workflow() -> dict:
    return {"chatbot_workflow": CHATBOT_WORKFLOW}


# ----------------------------------------------------------------------------
# Execução
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
