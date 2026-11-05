"""Only the bounded T17H lab can choose the explicit offline test mode."""

from app.api.booking import get_booking_engine
from app.api.workbench import get_workflow_service
from app.core.config import get_settings
from app.main import create_app
from app.workbench.workflow import WorkflowService

settings = get_settings()
assert settings.mysql_database.startswith("agentchaos_t17h_verify_")
assert not settings.deepseek_api_key.get_secret_value()
app = create_app()
app.dependency_overrides[get_workflow_service] = lambda: WorkflowService(
    get_booking_engine(), settings, mode="offline_test_double"
)
