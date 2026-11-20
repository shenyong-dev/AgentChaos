import json

import pytest

from app.external.dify import chat_result


def wire_events(*events):
    body = b"\xef\xbb\xbfevent: ping\r\n\r\n"
    for event in events:
        body += b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\r\n\r\n"
    return (body[offset : offset + 7] for offset in range(0, len(body), 7))


MESSAGE = {"event": "message", "answer": "售后服务", "conversation_id": "conversation-1"}
END = {"event": "message_end", "metadata": {"retriever_resources": [{"document_id": "policy"}]}}
STARTED = {"event": "workflow_started"}
FINISHED = {"event": "workflow_finished", "data": {"status": "succeeded"}}


@pytest.mark.parametrize("suffix", [(END,), (END, FINISHED), (FINISHED, END)])
def test_dify_full_reply_and_retrieval_metadata(suffix):
    body = (STARTED, MESSAGE, *suffix) if len(suffix) > 1 else (MESSAGE, END)
    assert chat_result(wire_events(*body)) == {
        "answer": "售后服务",
        "conversation_id": "conversation-1",
        "metadata": END["metadata"],
    }


@pytest.mark.parametrize(
    "events",
    [
        (MESSAGE,),
        (MESSAGE, {"event": "error"}, END),
        (STARTED, MESSAGE, END),
        (STARTED, MESSAGE, END, {"event": "workflow_finished", "data": {"status": "failed"}}),
        (MESSAGE, {**END, "conversation_id": "another-conversation"}),
    ],
)
def test_dify_does_not_promote_a_partial_or_mismatched_stream(events):
    with pytest.raises(ValueError):
        chat_result(wire_events(*events))
