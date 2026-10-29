"""Freeze a reviewed catalog in existing case/run JSON, preserving the 16 tables."""

from app.agents.errors import AgentError
from app.agents.versions import canonical_hash
from app.schemas.metamorphic import RelationCatalog


def frozen_relations(cases):
    metadata = [c["content"]["fixture"].get("metamorphic") for c in cases]
    if all(x is None for x in metadata):
        return None
    try:
        first = metadata[0]
        if not all(x == first for x in metadata):
            raise ValueError("All members must use exactly the same catalog/review")
        catalog = RelationCatalog.model_validate(first["catalog"])
        data = catalog.model_dump(mode="json")
        digest = canonical_hash(data)
        review = first["review"]
        if (
            review["catalog_hash"] != digest
            or review["decision"] != "confirmed"
            or review["source"] != "direct_user_confirmation"
            or not review["confirmed_at"]
            or not review["statement"]
        ):
            raise ValueError("A separate, hash-bound human review receipt is required")
        # This receipt is written locally from the actual human reply by the harness;
        # no HTTP endpoint imports/approves arbitrary catalogs in T10.
        by_key = {c["content"]["case_key"]: c for c in cases}
        if len(by_key) != len(cases) or len(cases) != len(catalog.members):
            raise ValueError("One frozen case revision per member is required")
        member_cases = {}
        for member in catalog.members:
            key = f"T10-{member.key}-{digest[:12].upper()}"
            case = by_key[key]
            content = case["content"]
            if (
                content["family_key"] != catalog.family_key
                or content["dataset_split"] != catalog.dataset_split
                or content["turns"] != [{"user": t} for t in member.turns]
                or content["fixture"]["fixture"] != catalog.fixture
            ):
                raise ValueError("Catalog member differs from frozen case")
            if case["dsl"]["reference_clock"] != data["reference_clock"]:
                raise ValueError("Member reference clocks differ")
            member_cases[member.key] = case["case_revision_id"]
        return {
            "catalog": data,
            "catalog_hash": digest,
            "review": review,
            "member_cases": member_cases,
        }
    except (ValueError, TypeError, KeyError):
        raise AgentError(
            "relation_catalog_invalid", "关系目录、人工审核或成员冻结条件不一致。", 409
        ) from None
