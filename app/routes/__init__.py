from .webhook import webhook_bp
from .health import health_bp
from .analytics import analytics_bp
from .recommendations import recommendation_bp
from .abtests import abtest_bp
from .feedback import feedback_bp
from .compliance import compliance_bp
from .agenda import agenda_bp
from .admin_projects import bp as admin_projects_bp

__all__ = [
    "webhook_bp",
    "health_bp",
    "analytics_bp",
    "recommendation_bp",
    "abtest_bp",
    "feedback_bp",
    "compliance_bp",
    "agenda_bp",
    "admin_projects_bp",
]
