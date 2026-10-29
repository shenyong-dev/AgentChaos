"""Explicit semantic relations, not inferred from reply similarity or a judge."""

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.contracts import ContractModel

IntentField = Literal["date", "start_time", "end_time", "attendees", "room_code"]


class Intent(ContractModel):
    date: date
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    attendees: int = Field(ge=1, strict=True)
    room_code: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,31}$")

    @model_validator(mode="after")
    def interval(self):
        if self.start_time >= self.end_time:
            raise ValueError("Same-day end must follow start")
        return self


class Member(ContractModel):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9-]{1,39}$")
    turns: list[str] = Field(min_length=1, max_length=50)
    intent: Intent
    booking_count: Literal[0, 1]

    @model_validator(mode="after")
    def bounded_turns(self):
        if any(not t.strip() or len(t) > 12_000 for t in self.turns):
            raise ValueError("Each user turn must contain 1..12000 characters")
        return self


class Relation(ContractModel):
    key: str = Field(pattern=r"^R-[A-Z0-9-]{1,40}$")
    title: str = Field(min_length=1, max_length=100)
    base: str
    variant: str
    kind: Literal["equivalent", "changed"]
    changed_fields: list[IntentField] = Field(max_length=5)
    rationale: str = Field(min_length=1, max_length=1500)


class RelationCatalog(ContractModel):
    schema_version: Literal["1.0"]
    family_key: str = Field(min_length=1, max_length=100)
    dataset_split: Literal["dev", "calibration", "holdout"]
    reference_clock: AwareDatetime
    fixture: Literal["booking-default-v1"]
    source: str = Field(min_length=1, max_length=1000)
    members: list[Member] = Field(min_length=2, max_length=30)
    relations: list[Relation] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def explicit_relations(self):
        members = {m.key: m for m in self.members}
        if len(members) != len(self.members):
            raise ValueError("Duplicate member key")
        if len({r.key for r in self.relations}) != len(self.relations):
            raise ValueError("Duplicate relation key")
        used = set()
        for r in self.relations:
            if r.base not in members or r.variant not in members or r.base == r.variant:
                raise ValueError("Relation must reference two distinct catalog members")
            a, b = members[r.base], members[r.variant]
            actual = {
                k for k in Intent.model_fields if getattr(a.intent, k) != getattr(b.intent, k)
            }
            if len(set(r.changed_fields)) != len(r.changed_fields) or actual != set(
                r.changed_fields
            ):
                raise ValueError("Changed fields must exactly describe the annotated intent delta")
            if (r.kind == "equivalent") != (not actual):
                raise ValueError("Equivalent and changed relations cannot be interchanged")
            if r.kind == "equivalent" and a.booking_count != b.booking_count:
                raise ValueError("Equivalent intent cannot have different booking counts")
            used.update((r.base, r.variant))
        if used != members.keys():
            raise ValueError("Unreferenced member")
        return self
