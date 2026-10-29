"""Trusted local harness import. No client-provided approval API in T10."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.executors.control import advisory
from app.executors.service import case_content
from app.models import TestCase, User


def register_catalog(engine, catalog, review):
    data = catalog.model_dump(mode="json")
    digest = canonical_hash(data)
    if (
        review.get("catalog_hash") != digest
        or review.get("decision") != "confirmed"
        or review.get("source") != "direct_user_confirmation"
    ):
        raise AgentError(
            "relation_review_required", "关系需单独的人工确认回执，且绑定确切目录哈希。", 409
        )
    # All family registration through this path is serialized, including another split.
    # Existing DB triggers also reject approved family/split conflicts.
    with advisory(engine, "case-family:" + catalog.family_key) as connection:
        with Session(connection) as session, session.begin():
            prior = list(
                session.scalars(
                    select(TestCase)
                    .where(
                        TestCase.family_key == catalog.family_key,
                        TestCase.review_status == "approved",
                    )
                    .with_for_update()
                )
            )
            if any(c.dataset_split != catalog.dataset_split for c in prior):
                raise AgentError("relation_family_split", "同家族已登记到其他数据分区。", 409)
            reviewer = session.scalar(select(User.id).where(User.role == "reviewer", User.enabled))
            if reviewer is None:
                raise AgentError("relation_reviewer_missing", "缺少服务端审核登记账号。", 409)
            ids = []
            for member in catalog.members:
                expected = member.intent.model_dump(mode="json")
                assertions = [
                    {"type": "booking_count", "equals": member.booking_count},
                    {
                        "type": "state_matches",
                        "fields": {
                            k: expected[k] for k in ("date", "start_time", "attendees", "room_code")
                        },
                    },
                ]
                if member.booking_count:
                    assertions.append(
                        {
                            "type": "booking_matches",
                            **{k: expected[k] for k in ("date", "attendees", "room_code")},
                        }
                    )
                row = TestCase(
                    case_key=f"T10-{member.key}-{digest[:12].upper()}",
                    revision=1,
                    family_key=catalog.family_key,
                    category="robustness",
                    dataset_split=catalog.dataset_split,
                    source="T10_user_confirmed_development_relations_not_T13_formal_dataset",
                    turns_json=[{"user": t} for t in member.turns],
                    fixture_json={
                        "fixture": catalog.fixture,
                        "reference_clock": data["reference_clock"],
                        "metamorphic": {"catalog": data, "review": review},
                    },
                    expected_json={
                        "schema_version": "1.1",
                        "assertions": assertions,
                        "task_goal": {
                            "start_at": f"{expected['date']}T{expected['start_time']}:00+08:00",
                            "end_at": f"{expected['date']}T{expected['end_time']}:00+08:00",
                            "attendees": expected["attendees"],
                            "room_code": expected["room_code"],
                        },
                    },
                    review_status="approved",
                    reviewer_id=reviewer,
                    reviewed_at=datetime.fromisoformat(review["confirmed_at"]),
                    content_hash="0" * 64,
                )
                row.content_hash = canonical_hash(case_content(row))
                existing = session.scalar(
                    select(TestCase).where(
                        TestCase.case_key == row.case_key, TestCase.revision == 1
                    )
                )
                if existing:
                    if existing.content_hash != row.content_hash:
                        raise AgentError(
                            "relation_revision_conflict", "目录修订已冻结，内容变化需新修订。", 409
                        )
                    ids.append(existing.id)
                else:
                    session.add(row)
                    session.flush()
                    ids.append(row.id)
            return ids
