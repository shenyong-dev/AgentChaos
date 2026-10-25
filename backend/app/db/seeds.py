"""Idempotent local demonstration data. Never replaces revisions or resets passwords."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.passwords import hash_password, verify_password
from app.db.fixtures import DEMO_SCOPE, ensure_rooms
from app.models import KnowledgeDocument, Target, User

RULE_PATH = Path(__file__).resolve().parents[3] / "knowledge" / "booking-rules-v1.md"


class SeedConflict(ValueError):
    pass


def seed_database(engine: Engine, password: str) -> dict[str, int]:
    if not password:
        raise ValueError("Set DEMO_SEED_PASSWORD in the ignored local .env before seeding")
    content = RULE_PATH.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()
    added = {"users": 0, "targets": 0, "knowledge_documents": 0, "rooms": 0}
    with engine.connect() as connection:
        lock_name = "ac:seed:" + str(connection.engine.url.database)
        acquired = connection.scalar(text("SELECT GET_LOCK(:name, 5)"), {"name": lock_name})
        connection.commit()
        if acquired != 1:
            raise SeedConflict("Seed lock is busy")
        try:
            with Session(connection) as session, session.begin():
                users = {}
                for username, role in (
                    ("demo_operator", "operator"),
                    ("demo_reviewer", "reviewer"),
                ):
                    user = session.scalar(select(User).where(User.username == username))
                    if user is None:
                        user = User(
                            username=username,
                            role=role,
                            enabled=True,
                            password_hash=hash_password(password),
                        )
                        session.add(user)
                        session.flush()
                        added["users"] += 1
                    elif (
                        user.role != role
                        or not user.enabled
                        or not verify_password(password, user.password_hash)
                    ):
                        raise SeedConflict(
                            "Existing demonstration account differs; nothing overwritten"
                        )
                    users[role] = user
                owner = users["operator"]
                target = session.scalar(
                    select(Target).where(
                        Target.owner_id == owner.id,
                        Target.name == "场地预约实验对象",
                        Target.adapter_type == "langgraph_booking",
                    )
                )
                if target is None:
                    session.add(
                        Target(
                            name="场地预约实验对象",
                            adapter_type="langgraph_booking",
                            owner_id=owner.id,
                            description="自建实验场景；T02仅有数据，智能体在T04实现。",
                            capabilities_json={"agent_implemented": False, "stage": "T02"},
                        )
                    )
                    added["targets"] += 1
                document = session.scalar(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.document_key == "booking-rules",
                        KnowledgeDocument.revision == 1,
                    )
                )
                applicability = {"scenario": "experimental_booking", "rule_version": "v1"}
                if document is None:
                    session.add(
                        KnowledgeDocument(
                            document_key="booking-rules",
                            revision=1,
                            title="场地预约实验规则 v1",
                            source_type="experiment",
                            source_ref="knowledge/booking-rules-v1.md",
                            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                            valid_to=None,
                            applicability_json=applicability,
                            content=content,
                            content_hash=digest,
                        )
                    )
                    added["knowledge_documents"] += 1
                elif (
                    document.content_hash != digest
                    or document.content != content
                    or document.applicability_json != applicability
                ):
                    raise SeedConflict("Knowledge revision already differs; create a new revision")
                added["rooms"] = ensure_rooms(session, DEMO_SCOPE)
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
            connection.commit()
    return added
