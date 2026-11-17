"""Check explicit source bindings without importing application code or accessing services."""

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parents[3]
PROFILES = ROOT / "docs/design/external-agent/profiles"
OPERATIONS = {"manifest", "prepare", "execute", "snapshot", "evaluate", "cancel", "lookup"}
CAPABILITIES = {
    "dialogue",
    "tool_events",
    "business_facts",
    "retrieval_evidence",
    "domain_coverage",
    "ai_judge",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Capability(Strict):
    status: Literal["supported", "conditional", "not_applicable"]
    boundary: str = Field(min_length=1, max_length=400)


class Binding(Strict):
    path: str = Field(pattern=r"^backend/[A-Za-z0-9_/-]+\.py$")
    symbol: str = Field(pattern=r"^[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)?$")
    parameters: list[str] = Field(min_length=0, max_length=16)
    responsibility: str = Field(min_length=1, max_length=300)


class Profile(Strict):
    schema_version: Literal["ac-target-contract-draft-1"]
    target_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    stage: Literal["reference_inventory", "external_template"]
    author_relation: Literal["same_project_developer", "pending_external"]
    network_enabled: bool = Field(json_schema_extra={"const": False})
    trace_schema: Literal["1.3"]
    case_schema: str = Field(min_length=1, max_length=40)
    execution_shape: Literal["worker_task", "domain_port", "pending_http_bridge"]
    facts_origin: Literal["platform_sql", "reference_sqlite", "pending_external"]
    isolation: Literal["database_owner_scope", "pending_external"]
    reset_policy: Literal["new_scope_only"]
    cancellation: Literal["cooperative_no_rollback", "pending_external"]
    unknown_policy: Literal["retain_key_no_replay"]
    capabilities: dict[str, Capability]
    operations: dict[str, Binding | None]

    @model_validator(mode="after")
    def coherent(self):
        if self.network_enabled is not False:
            raise ValueError("Declarations cannot enable network access")
        if set(self.operations) != OPERATIONS or set(self.capabilities) != CAPABILITIES:
            raise ValueError("Every contract operation and capability must be explicit")
        if self.stage == "reference_inventory":
            if (
                self.author_relation != "same_project_developer"
                or self.execution_shape == "pending_http_bridge"
                or self.facts_origin == "pending_external"
                or self.isolation != "database_owner_scope"
                or self.cancellation != "cooperative_no_rollback"
                or any(binding is None for binding in self.operations.values())
            ):
                raise ValueError("Reference inventory requires existing source bindings")
        elif (
            self.author_relation != "pending_external"
            or self.execution_shape != "pending_http_bridge"
            or self.facts_origin != "pending_external"
            or self.isolation != "pending_external"
            or self.cancellation != "pending_external"
            or any(binding is not None for binding in self.operations.values())
            or any(c.status != "conditional" for c in self.capabilities.values())
        ):
            raise ValueError("An external template cannot claim implemented capabilities")
        return self


def load_profile(path):
    if path.stat().st_size > 64_000:
        raise ValueError("Profile exceeds declaration limit")

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate declaration field")
            value[key] = item
        return value

    return Profile.model_validate(json.loads(path.read_bytes(), object_pairs_hook=unique_object))


def check_binding(binding, *, root=ROOT):
    root = root.resolve()
    path = (root / binding.path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Source binding leaves project root")
    raw = path.read_bytes()
    tree = ast.parse(raw)
    parts = binding.symbol.split(".")
    body = tree.body
    for index, part in enumerate(parts):
        matches = [
            node
            for node in body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == part
        ]
        if len(matches) != 1:
            raise ValueError("Source symbol is missing or ambiguous")
        node = matches[0]
        if index != len(parts) - 1 and not isinstance(node, ast.ClassDef):
            raise ValueError("Source binding parent must be a class")
        body = node.body
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError("Source binding must identify a function")
    parameters = [
        arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    ]
    if len(set(binding.parameters)) != len(binding.parameters) or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", p) or p not in parameters
        for p in binding.parameters
    ):
        raise ValueError("Required parameters no longer match source signature")
    positional = [*node.args.posonlyargs, *node.args.args]
    required = {arg.arg for arg in positional[: len(positional) - len(node.args.defaults)]}
    required.update(
        arg.arg
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
        if default is None
    )
    if len(parts) == 2:
        required.discard("self")
        required.discard("cls")
    if not required.issubset(binding.parameters):
        raise ValueError("Source requires undeclared parameters")
    return {
        "path": binding.path,
        "symbol": binding.symbol,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "parameters": parameters,
    }


def inspect_profile(profile, *, root=ROOT):
    bindings = {
        name: check_binding(binding, root=root) if binding else None
        for name, binding in profile.operations.items()
    }
    return {
        "target_id": profile.target_id,
        "stage": profile.stage,
        "declaration_sha256": hashlib.sha256(
            json.dumps(profile.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "source_bindings": bindings,
        "capabilities": {key: value.model_dump() for key, value in profile.capabilities.items()},
        "checks": {
            "declaration_shape": "passed",
            "source_symbols": "passed" if any(bindings.values()) else "not_applicable",
        },
        "runtime_conformance": "not_tested",
        "independent_external_validation": False,
        "registration_ready": False,
        "network_requests": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, action="append")
    parser.add_argument(
        "--output", type=Path, required=True, help="New report file, never overwritten"
    )
    args = parser.parse_args(argv)
    try:
        paths = args.profile or sorted(PROFILES.glob("*.json"))
        if not paths:
            raise ValueError("No declarations selected")
        profiles = [load_profile(path) for path in paths]
        if len({profile.target_id for profile in profiles}) != len(profiles):
            raise ValueError("Duplicate target declarations")
        result = {
            "schema": "p06-source-inventory-1",
            "scope": "offline declaration and AST binding checks only",
            "profiles": [inspect_profile(profile) for profile in profiles],
        }
        # Validation completes before any output is created. Exclusive create preserves old reports.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, SyntaxError, ValidationError):
        # Do not echo invalid JSON, paths or Pydantic input values: they may contain secrets.
        parser.exit(
            2, "Contract check failed; input invalid, source drift or output already exists.\n"
        )
    print("Offline contract inventory saved; no runtime or external validation performed.")


if __name__ == "__main__":
    main()
