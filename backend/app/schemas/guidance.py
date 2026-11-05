"""Help accepts typed identifiers, never URLs, commands, prompts or business decisions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PageID = Literal[
    "workspace",
    "targets",
    "library",
    "workbench",
    "runs",
    "interactive",
    "cases",
    "judging",
    "semantic",
    "reports",
    "environment",
]
ObjectKind = Literal[
    "case", "run", "execution", "version", "generation", "calibration", "interaction"
]


class GuideContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: PageID
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_kind: ObjectKind | None = None
    object_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def paired(self):
        if (self.object_kind is None) != (self.object_id is None):
            raise ValueError("Object kind and id must be provided together")
        allowed = {
            "case": {"library"},
            "run": {"runs", "reports", "workbench"},
            "execution": {"runs", "semantic"},
            "version": {"targets"},
            "generation": {"cases"},
            "calibration": {"judging"},
            "interaction": {"interactive"},
        }
        if self.object_kind and self.page not in allowed[self.object_kind]:
            raise ValueError("Object does not belong to this page context")
        return self


class GuideAction(GuideContext):
    action_id: str = Field(pattern=r"^[a-z]+[a-z.]{1,63}$")
