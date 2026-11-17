"""Reader projection and safe navigation only; no participant or model calls."""

from copy import deepcopy

from test_p04_trial import Double

from benchmarks.p04_trial.case_reader import SECTIONS, citation_links, render_case


def test_all_product_sections_have_actual_focusable_targets_and_keep_declared_state_separate():
    task = Double().check()["cases"][0]
    body = render_case(task)
    for _, target, label, _ in SECTIONS:
        assert 'id="' + target + '" class="case-section" tabindex="-1"' in body
        assert label in body
    assert "X01" in body and "机械数量检查" in body
    assert "0 条（用例声明）" in body
    assert "Mechanical expectation only" in body


def test_case_projection_escapes_data_and_does_not_treat_missing_state_as_zero():
    task = deepcopy(Double().check()["cases"][0])
    task["snapshot"]["guidance"]["turns"][0]["user"] = "<script>mechanical</script>"
    del task["snapshot"]["guidance"]["prerequisites"]["initial_state"]["active_bookings"]
    body = render_case(task)
    assert "<script>mechanical</script>" not in body
    assert "&lt;script&gt;mechanical&lt;/script&gt;" in body
    assert "0 条（用例声明）" not in body
    assert "未记录" in body


def test_citations_only_use_fixed_anchors_for_matching_cited_case_snapshot():
    runtime = Double()
    task = runtime.check()["cases"][0]
    result = runtime.ask("mechanical", "mechanical-request")
    body = citation_links(task, result)
    assert body.count('class="source-ref"') == 4
    assert 'href="#guide-before"' in body
    result["sources"][0]["content"]["locations"]["turns"] = "javascript:foreign"
    assert "javascript:" not in citation_links(task, result)
    result["sources"][0]["content"]["id"] = "different"
    assert citation_links(task, result) == ""
    result["sources"][0]["content"]["id"] = "700001"
    result["sources"][0]["content"]["content_hash"] = "foreign"
    assert citation_links(task, result) == ""
