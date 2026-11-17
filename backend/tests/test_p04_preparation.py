"""Preparation boundaries only; no human answers, provider or business execution."""

from copy import deepcopy

import pytest

from benchmarks.p04_preparation.pack import load_inputs, prepare, validate, verify


def test_preparation_stays_offline_and_existing_output_is_never_replaced(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Preparation must not access the network")

    monkeypatch.setattr("socket.socket", forbidden)
    folder = tmp_path / "preparation"
    original = prepare(folder)
    assert verify(folder) == original
    assert original["provider_calls"] == original["participant_records"] == 0
    brief = (folder / "participant-brief.md").read_text(encoding="utf-8")
    assert "我的判断：\n" in brief and "judgment_checks" not in brief
    assert "expected_counts" not in brief
    saved = (folder / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        prepare(folder)
    assert (folder / "manifest.json").read_bytes() == saved


@pytest.mark.parametrize("change", ["paid", "old_task", "protected", "human_gold", "turn"])
def test_draft_cannot_become_an_authorization_or_reuse_protected_or_old_tasks(change):
    protocol, tasks, reference = deepcopy(load_inputs())
    if change == "paid":
        protocol["provider_execution_enabled"] = True
    elif change == "old_task":
        tasks["tasks"][0]["case_key"] = "T13-N003"
    elif change == "protected":
        tasks["tasks"][0]["source_split"] = "holdout"
    elif change == "human_gold":
        reference["status"] = "human_verified"
    else:
        tasks["tasks"][0]["snapshot"]["turns"][0] = "Mechanical tampering control"
    with pytest.raises(ValueError):
        validate(protocol, tasks, reference)


def test_changed_saved_brief_is_rejected_before_it_can_be_reused(tmp_path):
    folder = tmp_path / "preparation"
    prepare(folder)
    (folder / "participant-brief.md").write_text("Changed control", encoding="utf-8")
    with pytest.raises(ValueError, match="member changed"):
        verify(folder)
