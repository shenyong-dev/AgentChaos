"""Owner-bound append-only SQL evidence; raw bytes live only under the D-drive workspace."""

import hashlib
import json
from uuid import uuid4

from app.agents.errors import AgentError
from app.core.config import PROJECT_ROOT
from app.evidence.recorder import redact
from app.models import Artifact

ROOT = PROJECT_ROOT / ".local/case-generation"


def append(session, settings, owner, batch, kind, value, metadata):
    namespace = hashlib.sha256(session.bind.engine.url.database.encode()).hexdigest()[:12]
    directory = ROOT / namespace / str(batch)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (uuid4().hex + ".json")
    data = (
        json.dumps(
            redact(value, settings), ensure_ascii=False, sort_keys=True, indent=2, default=str
        )
        + "\n"
    ).encode()
    if len(data) > 2_000_000:
        raise AgentError("casegen_evidence_limit", "生成证据超过单份上限。", 503)
    with path.open("xb") as file:
        file.write(data)
    row = Artifact(
        run_id=None,
        owner_id=owner,
        kind=kind,
        relative_path=path.relative_to(PROJECT_ROOT).as_posix(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        generated_by_revision="T11-v1",
        metadata_json={"generation_id": str(batch), **metadata},
    )
    session.add(row)
    session.flush()
    return row


def read(row):
    path = (PROJECT_ROOT / row.relative_path).resolve()
    if not path.is_relative_to(ROOT.resolve()) or row.run_id is not None:
        raise AgentError("casegen_evidence_invalid", "生成证据路径或归属不符。", 503)
    try:
        data = path.read_bytes()
        if len(data) != row.size_bytes or hashlib.sha256(data).hexdigest() != row.sha256:
            raise ValueError()
        return json.loads(data)
    except (OSError, ValueError):
        raise AgentError("casegen_evidence_invalid", "生成证据缺失或哈希不符。", 503) from None
