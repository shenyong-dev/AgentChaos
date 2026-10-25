"""Local historical observation, bound to credentials without exposing their digest."""

import hashlib
import json

from app.core.config import PROJECT_ROOT

PROBE_PATH = PROJECT_ROOT / ".local/t04/model-observation.json"


def identity(settings):
    return {
        "key_digest": hashlib.sha256(
            settings.deepseek_api_key.get_secret_value().encode()
        ).hexdigest(),
        "base_url": settings.deepseek_base_url,
        "model": settings.agent_model,
        "thinking": settings.agent_thinking,
    }


def remember_success(settings, record):
    PROBE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PROBE_PATH.with_suffix("." + record["call_id"] + ".tmp")
    temporary.write_text(
        json.dumps(
            {**identity(settings), "checked_at": record["started_at"], "call_id": record["call_id"]}
        ),
        encoding="utf-8",
    )
    temporary.replace(PROBE_PATH)


def historical_observation(settings):
    try:
        value = json.loads(PROBE_PATH.read_text(encoding="utf-8"))
        if all(value.get(key) == expected for key, expected in identity(settings).items()):
            return {key: value[key] for key in ("checked_at", "call_id")}
    except (OSError, ValueError, KeyError):
        pass
    return None
