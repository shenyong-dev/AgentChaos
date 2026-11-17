import json

import pytest
from pydantic import ValidationError

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.core.config import Settings
from app.external.registry import entries
from app.external.web_config import (
    Connection,
    entry_for_draft,
    profiles_for_ui,
    save_connection,
    store_path,
)


@pytest.fixture
def local_profile(tmp_path):
    path = tmp_path / "policy.json"
    profile = {
        "id": "approved-service",
        "label": "审核过的服务",
        "endpoint": "http://127.0.0.1:18072/agentchaos/v1",
        "pinned_ip": "127.0.0.1",
        "network_zone": "approved_private",
        "credential_env": "AGENTCHAOS_EXTERNAL_APPROVED_TEST",
    }
    path.write_text(json.dumps({"profiles": [profile]}), encoding="utf-8")
    settings = Settings(
        app_env="test", external_agent_registry_path="", external_agent_web_policy_path=str(path)
    )
    form = Connection(
        profile_id=profile["id"], name=profile["label"], revision="test-v1", timeout_seconds=12
    )
    return path, profile, settings, form


def test_operator_payload_cannot_supply_a_new_destination_or_credential(local_profile):
    _, _, settings, form = local_profile
    for field in ("endpoint", "pinned_ip", "credential_env"):
        with pytest.raises(ValidationError):
            Connection.model_validate({**form.model_dump(), field: "ignored"})
    with pytest.raises(AgentError):
        entry_for_draft(settings, form.model_copy(update={"profile_id": "not-approved"}))
    assert entries(settings) == {}
    assert "credential_env" not in profiles_for_ui(settings)[0]
    assert not store_path(settings).exists()


def test_saved_configuration_is_idempotent_and_isolated_by_deployment(local_profile):
    _, _, settings, form = local_profile
    entry = entry_for_draft(settings, form)
    digest = canonical_hash(entry.model_dump())
    save_connection(settings, form, digest)
    before = store_path(settings).read_bytes()
    save_connection(settings, form, digest)
    assert before == store_path(settings).read_bytes()
    assert entries(settings) == {"external_http:approved-service": entry}
    assert entries(settings.model_copy(update={"mysql_database": "other_agentchaos"})) == {}
    with pytest.raises(AgentError):
        save_connection(settings, form.model_copy(update={"revision": "different"}), digest)


def test_profile_change_does_not_retarget_a_saved_connection(local_profile):
    path, profile, settings, form = local_profile
    digest = canonical_hash(entry_for_draft(settings, form).model_dump())
    save_connection(settings, form, digest)
    changed = {**profile, "endpoint": "http://127.0.0.1:18073/agentchaos/v1"}
    path.write_text(json.dumps({"profiles": [changed]}), encoding="utf-8")
    assert entries(settings) == {}
