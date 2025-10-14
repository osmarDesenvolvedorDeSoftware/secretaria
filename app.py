"""Aplicação Flask "Secretária Osmar".

Este arquivo contém a aplicação principal com:
- Configuração de banco de dados SQLite via SQLAlchemy;
- Sistema simples de login usando Flask-Login;
- Rotas do painel administrativo para gerenciar projetos e reuniões;
- API REST consumida pelo chatbot externo;
- Função mock para integração com CallMeBot (WhatsApp).

Execute com `python app.py`.
"""

from datetime import datetime
from typing import Optional

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    flash,
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
from sqlalchemy import event
import requests

# -----------------------------------------------------------------------------
# Configuração básica da aplicação
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.config.update(
    SECRET_KEY="segredo-super-seguro",
    SQLALCHEMY_DATABASE_URI="sqlite:///secretaria_osmar.db",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    ADMIN_USERNAME="osmar",
    ADMIN_PASSWORD="123456",  # Em produção utilize variáveis de ambiente!
    CALLMEBOT_PHONE="+550000000000",
    CALLMEBOT_API_KEY="CHAVE_DE_EXEMPLO",
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


# -----------------------------------------------------------------------------
# Modelos do banco de dados
# -----------------------------------------------------------------------------
class Project(db.Model):
    """Tabela de projetos apresentados aos clientes."""

    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    tech_stack = db.Column(db.Text)
    demo_url = db.Column(db.String(255))
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        """Representação em dicionário utilizada pelas APIs."""

        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tech_stack": self.tech_stack,
            "demo_url": self.demo_url,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Meeting(db.Model):
    """Tabela de reuniões/agendamentos."""

    __tablename__ = "meetings"

    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(255), nullable=False)
    contact = db.Column(db.String(255))
    datetime = db.Column(db.DateTime)
    status = db.Column(db.String(50), default="pending")
    notes = db.Column(db.Text)

    def to_dict(self) -> dict:
        """Representação em dicionário utilizada pelas APIs."""

        return {
            "id": self.id,
            "client_name": self.client_name,
            "contact": self.contact,
            "datetime": self.datetime.isoformat() if self.datetime else None,
            "status": self.status,
            "notes": self.notes,
        }


# -----------------------------------------------------------------------------
# Criação automática do banco de dados na primeira execução
# -----------------------------------------------------------------------------
@event.listens_for(Project.__table__, "after_create")
def insert_sample_projects(*_, **__):
    """Insere projetos de exemplo para facilitar a demonstração."""

    demo_projects = [
        Project(
            name="App Android com QR Code",
            description="Aplicativo Android em Kotlin com leitura de QR Code e integração com APIs.",
            tech_stack="Kotlin, MVVM, Retrofit, Firebase",
            demo_url="https://github.com/osmar/android-qrcode-demo",
        ),
        Project(
            name="Dashboard de automação em Python",
            description="Dashboard Flask para monitoramento de robôs RPA e integrações com Google.",
            tech_stack="Python, Flask, RPA, Google APIs",
            demo_url="https://github.com/osmar/python-dashboard",
        ),
    ]
    db.session.bulk_save_objects(demo_projects)
    db.session.commit()


with app.app_context():
    db.create_all()


# -----------------------------------------------------------------------------
# Usuário administrativo (login simples)
# -----------------------------------------------------------------------------
class AdminUser(UserMixin):
    """Representa o usuário administrador único do sistema."""

    def __init__(self, user_id: str, username: str):
        self.id = user_id
        self.username = username


@login_manager.user_loader
def load_user(user_id: str) -> Optional[AdminUser]:
    """Carrega o usuário para a sessão do Flask-Login."""

    if user_id == "admin":
        return AdminUser("admin", app.config["ADMIN_USERNAME"])
    return None


# -----------------------------------------------------------------------------
# Integração (mock) com CallMeBot para notificar o Osmar via WhatsApp
# -----------------------------------------------------------------------------
def notify_osmar_via_callmebot(message: str) -> None:
    """Envia notificação via CallMeBot.

    Esta função demonstra como a integração seria realizada. O número de telefone
    e a API key devem ser configurados via variáveis de ambiente em produção.
    Caso a chamada à API falhe, registramos no log da aplicação.
    """

    phone = app.config.get("CALLMEBOT_PHONE")
    api_key = app.config.get("CALLMEBOT_API_KEY")
    if not phone or not api_key:
        app.logger.warning("Configuração do CallMeBot ausente. Mensagem: %s", message)
        return

    url = "https://api.callmebot.com/whatsapp.php"
    payload = {
        "phone": phone,
        "text": message,
        "apikey": api_key,
    }

    try:
        response = requests.get(url, params=payload, timeout=10)
        response.raise_for_status()
        app.logger.info("Notificação enviada para Osmar: %s", message)
    except requests.RequestException as exc:
        app.logger.error("Falha ao enviar notificação: %s", exc)


# -----------------------------------------------------------------------------
# Rotas públicas e de autenticação
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    """Redireciona para o painel administrativo ou para o login."""

    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Tela de login simples protegendo o painel administrativo."""

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if (
            username == app.config["ADMIN_USERNAME"]
            and password == app.config["ADMIN_PASSWORD"]
        ):
            user = AdminUser("admin", username)
            login_user(user)
            flash("Login realizado com sucesso!", "success")
            return redirect(url_for("dashboard"))

        flash("Credenciais inválidas.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    """Finaliza a sessão do usuário administrador."""

    logout_user()
    flash("Você saiu do painel.", "info")
    return redirect(url_for("login"))


# -----------------------------------------------------------------------------
# Rotas do painel administrativo (interface web)
# -----------------------------------------------------------------------------
@app.route("/admin")
@login_required
def dashboard():
    """Página inicial do painel com estatísticas rápidas."""

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
    """Lista e cadastra novos projetos."""

    if request.method == "POST":
        name = request.form.get("name")
        description = request.form.get("description")
        tech_stack = request.form.get("tech_stack")
        demo_url = request.form.get("demo_url")

        if not name:
            flash("Nome é obrigatório.", "danger")
        else:
            project = Project(
                name=name,
                description=description,
                tech_stack=tech_stack,
                demo_url=demo_url,
            )
            db.session.add(project)
            db.session.commit()
            flash("Projeto cadastrado com sucesso!", "success")
            return redirect(url_for("manage_projects"))

    projects = Project.query.order_by(Project.updated_at.desc()).all()
    return render_template("projects.html", projects=projects)


@app.route("/admin/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit_project(project_id: int):
    """Edita um projeto existente."""

    project = Project.query.get_or_404(project_id)
    if request.method == "POST":
        project.name = request.form.get("name")
        project.description = request.form.get("description")
        project.tech_stack = request.form.get("tech_stack")
        project.demo_url = request.form.get("demo_url")
        db.session.commit()
        flash("Projeto atualizado com sucesso!", "success")
        return redirect(url_for("manage_projects"))

    return render_template("project_form.html", project=project)


@app.route("/admin/projects/<int:project_id>/delete", methods=["POST"])
@login_required
def delete_project(project_id: int):
    """Remove um projeto."""

    project = Project.query.get_or_404(project_id)
    db.session.delete(project)
    db.session.commit()
    flash("Projeto removido com sucesso!", "info")
    return redirect(url_for("manage_projects"))


@app.route("/admin/meetings", methods=["GET", "POST"])
@login_required
def manage_meetings():
    """Visualiza e confirma reuniões agendadas."""

    if request.method == "POST":
        meeting_id = request.form.get("meeting_id")
        action = request.form.get("action")
        meeting = Meeting.query.get_or_404(meeting_id)

        if action == "confirm":
            meeting.status = "confirmed"
            db.session.commit()
            flash("Reunião confirmada! Osmar será notificado.", "success")
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


# -----------------------------------------------------------------------------
# API REST utilizada pelo chatbot externo
# -----------------------------------------------------------------------------
@app.route("/api/projects", methods=["GET"])
def api_projects():
    """Retorna todos os projetos em formato JSON."""

    projects = Project.query.order_by(Project.updated_at.desc()).all()
    return jsonify([project.to_dict() for project in projects])


@app.route("/api/projects/<int:project_id>", methods=["GET"])
def api_project_detail(project_id: int):
    """Retorna os detalhes de um único projeto."""

    project = Project.query.get_or_404(project_id)
    return jsonify(project.to_dict())


@app.route("/api/meetings", methods=["GET", "POST"])
def api_meetings():
    """Endpoint de reuniões utilizado pelo chatbot."""

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        client_name = data.get("client_name")
        contact = data.get("contact")
        datetime_str = data.get("datetime")
        notes = data.get("notes")

        if not client_name:
            return jsonify({"status": "error", "message": "Nome do cliente é obrigatório."}), 400

        meeting_datetime = None
        if datetime_str:
            try:
                meeting_datetime = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
            except ValueError:
                return (
                    jsonify({"status": "error", "message": "Formato de data/hora inválido. Use YYYY-MM-DD HH:MM."}),
                    400,
                )

        meeting = Meeting(
            client_name=client_name,
            contact=contact,
            datetime=meeting_datetime,
            notes=notes,
            status="pending",
        )
        db.session.add(meeting)
        db.session.commit()
        return jsonify({"status": "success", "message": "Reunião agendada com sucesso"})

    # GET - somente administradores autenticados podem consultar todas as reuniões
    if not current_user.is_authenticated:
        return jsonify({"error": "Autenticação requerida"}), 401

    meetings = Meeting.query.order_by(Meeting.datetime.desc()).all()
    return jsonify([meeting.to_dict() for meeting in meetings])


# -----------------------------------------------------------------------------
# Documentação do comportamento esperado para o chatbot externo
# -----------------------------------------------------------------------------
CHATBOT_WORKFLOW = """
Fluxo sugerido para o chatbot Secretária Osmar:
1. Cumprimentar o cliente de forma simpática e identificar o motivo do contato.
2. Consultar GET /api/projects para apresentar os principais trabalhos do Osmar.
3. Caso o cliente deseje avançar, solicitar dados para agendar (nome, contato, data e hora) e enviar via POST /api/meetings.
4. Após confirmação manual no painel (/admin/meetings), chamar notify_osmar_via_callmebot para avisar automaticamente o Osmar.
"""


@app.context_processor
def inject_chatbot_workflow():
    """Disponibiliza a documentação do chatbot nos templates."""

    return {"chatbot_workflow": CHATBOT_WORKFLOW}


# -----------------------------------------------------------------------------
# Execução da aplicação
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
