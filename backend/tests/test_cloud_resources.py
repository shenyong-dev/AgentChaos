from app.agents import retrieval


def test_cloud_dictionary_sharing_preserves_tokens_and_refreshes_changed_dictionary(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(retrieval, "PROJECT_ROOT", tmp_path)
    dictionary = tmp_path / "userdict.txt"
    dictionary.write_text("可靠性测试 10000 n\n", encoding="utf-8")
    retrieval._cloud_tokenizer.cache_clear()
    try:
        expected = retrieval._load_tokenizer(dictionary)
        shared = retrieval._cloud_tokenizer(
            str(dictionary), retrieval.digest(dictionary.read_text(encoding="utf-8"))
        )
        again = retrieval._cloud_tokenizer(
            str(dictionary), retrieval.digest(dictionary.read_text(encoding="utf-8"))
        )
        assert shared is again
        for text in ("A101容量20人，预约30分钟", "明天14:00到15:00", "可靠性测试与退款确认"):
            assert list(shared.cut(text, HMM=False)) == list(expected.cut(text, HMM=False))
        dictionary.write_text("可靠性测试 10000 n\n这是新的专有测试词 10000 n\n", encoding="utf-8")
        changed = retrieval._cloud_tokenizer(
            str(dictionary), retrieval.digest(dictionary.read_text(encoding="utf-8"))
        )
        assert changed is not shared
        assert "这是新的专有测试词" in list(changed.cut("这是新的专有测试词", HMM=False))
    finally:
        retrieval._cloud_tokenizer.cache_clear()
