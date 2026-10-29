from experiments.e1 import method_decisions


def test_enhanced_uses_objective_failure_without_rewriting_unknown_judge():
    failed = {"status": "failed", "timing": {"evaluation": {"verdict": "failed"}}}
    value = method_decisions(failed, True, {"status": "error"})
    assert value == {"answer_only": None, "rule_only": True, "enhanced": True}
    passed = {"status": "passed", "timing": {"evaluation": {"verdict": "passed"}}}
    assert method_decisions(passed, True, {"status": "inconclusive"})["enhanced"] is None
    assert method_decisions(passed, True, {"status": "failed"})["enhanced"] is True
    assert method_decisions(passed, True, {"status": "passed"})["enhanced"] is False


def test_infrastructure_or_incomplete_evidence_cannot_become_enhanced_pass():
    for status in ("error", "cancelled", "waiting_input"):
        value = {"status": status, "timing": {"evaluation": {"verdict": "failed"}}}
        assert method_decisions(value, True, {"status": "passed"})["enhanced"] is None
    value = {"status": "passed", "timing": {"evaluation": {"verdict": "passed"}}}
    assert method_decisions(value, False, {"status": "passed"})["enhanced"] is None
