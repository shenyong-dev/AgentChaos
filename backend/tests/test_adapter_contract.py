"""P06 source inventories are not runtime conformance or external validation."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from benchmarks.adapter_contract.check import (
    PROFILES,
    Binding,
    Profile,
    check_binding,
    inspect_profile,
    load_profile,
    main,
)


def declaration(name="booking"):
    return json.loads((PROFILES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["booking", "refund", "external-template"])
def test_real_source_inventory_does_not_grant_execution(name):
    result = inspect_profile(load_profile(PROFILES / f"{name}.json"))
    assert result["checks"]["declaration_shape"] == "passed"
    assert result["registration_ready"] is False
    assert result["independent_external_validation"] is False
    assert result["runtime_conformance"] == "not_tested"
    assert result["network_requests"] == 0
    bound = [value for value in result["source_bindings"].values() if value]
    assert len(bound) == (0 if name == "external-template" else 7)
    assert all(len(value["sha256"]) == 64 for value in bound)


@pytest.mark.parametrize(
    "change", ["operation", "capability", "network", "numeric_false", "extra", "null_binding"]
)
def test_incomplete_or_executable_declarations_are_rejected(change):
    value = declaration()
    if change == "operation":
        value["operations"].pop("lookup")
    elif change == "capability":
        value["capabilities"].pop("business_facts")
    elif change == "network":
        value["network_enabled"] = True
    elif change == "numeric_false":
        value["network_enabled"] = 0
    elif change == "null_binding":
        value["operations"]["execute"] = None
    else:
        value["endpoint"] = "https://unreviewed.invalid"
    with pytest.raises(ValidationError):
        Profile.model_validate(value)


@pytest.mark.parametrize("change", ["source", "implemented", "provenance"])
def test_external_template_cannot_claim_existing_implementation(change):
    value = declaration("external-template")
    if change == "source":
        value["operations"]["execute"] = declaration()["operations"]["execute"]
    elif change == "implemented":
        value["capabilities"]["dialogue"]["status"] = "supported"
    else:
        value["author_relation"] = "same_project_developer"
    with pytest.raises(ValidationError):
        Profile.model_validate(value)


def test_source_inspection_never_imports_or_executes(tmp_path):
    source = tmp_path / "backend/unsafe.py"
    source.parent.mkdir()
    marker = tmp_path / "executed.txt"
    source.write_text(
        f"raise RuntimeError({str(marker)!r})\n"
        "class Example:\n    def execute(self, owner, *, scope): pass\n",
        encoding="utf-8",
    )
    value = Binding(
        path="backend/unsafe.py",
        symbol="Example.execute",
        parameters=["owner", "scope"],
        responsibility="AST only",
    )
    assert check_binding(value, root=tmp_path)["parameters"] == ["self", "owner", "scope"]
    assert not marker.exists()


@pytest.mark.parametrize("change", ["missing_symbol", "wrong_parameter", "function_parent"])
def test_source_drift_and_bad_binding_are_rejected(tmp_path, change):
    source = tmp_path / "backend/port.py"
    source.parent.mkdir()
    source.write_text("def execute(owner):\n    def child(): pass\n", encoding="utf-8")
    symbol = (
        "absent"
        if change == "missing_symbol"
        else ("execute.child" if change == "function_parent" else "execute")
    )
    value = Binding(
        path="backend/port.py", symbol=symbol, parameters=["scope"], responsibility="drift control"
    )
    with pytest.raises(ValueError):
        check_binding(value, root=tmp_path)


def test_source_path_traversal_is_rejected():
    value = deepcopy(declaration()["operations"]["execute"])
    value["path"] = "backend/../../outside.py"
    with pytest.raises(ValidationError):
        Binding.model_validate(value)


@pytest.mark.parametrize("signature", ["owner, added", "owner, *, added"])
def test_new_required_parameter_cannot_pass_as_compatible_inventory(tmp_path, signature):
    source = tmp_path / "backend/port.py"
    source.parent.mkdir()
    source.write_text(f"def execute({signature}): pass\n", encoding="utf-8")
    value = Binding(
        path="backend/port.py",
        symbol="execute",
        parameters=["owner"],
        responsibility="new required parameter drift",
    )
    with pytest.raises(ValueError, match="undeclared"):
        check_binding(value, root=tmp_path)


def test_cli_keeps_existing_report_and_does_not_echo_invalid_inputs(tmp_path, capsys):
    output = tmp_path / "report.json"
    main(["--output", str(output)])
    original = output.read_bytes()
    capsys.readouterr()
    with pytest.raises(SystemExit) as blocked:
        main(["--output", str(output)])
    assert blocked.value.code == 2
    assert output.read_bytes() == original
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"api_key":"private-test-marker"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--profile", str(invalid), "--output", str(tmp_path / "new.json")])
    captured = capsys.readouterr()
    assert "private-test-marker" not in captured.out + captured.err
    assert not (tmp_path / "new.json").exists()


def test_duplicate_target_inventory_has_no_output(tmp_path):
    with pytest.raises(SystemExit):
        main(
            [
                "--profile",
                str(PROFILES / "booking.json"),
                "--profile",
                str(PROFILES / "booking.json"),
                "--output",
                str(tmp_path / "result.json"),
            ]
        )
    assert not (tmp_path / "result.json").exists()


def test_duplicate_json_keys_and_oversized_profiles_are_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"network_enabled":true,"network_enabled":false}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_profile(path)
    path.write_bytes(b" " * 64_001)
    with pytest.raises(ValueError):
        load_profile(path)
