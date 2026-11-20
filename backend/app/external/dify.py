"""Read a bounded Dify chat event stream and assemble its final reply."""

from app.external.transport import decode_json


def _events(chunks):
    pending = b""
    data = []
    first = True
    for chunk in chunks:
        *lines, pending = (pending + chunk).split(b"\n")
        for line in lines:
            line = line.removesuffix(b"\r")
            if first:
                line = line.removeprefix(b"\xef\xbb\xbf")
                first = False
            if line.startswith(b"data:"):
                data.append(line[5:].removeprefix(b" "))
            elif not line and data:
                event = decode_json(b"\n".join(data))
                data.clear()
                if not isinstance(event, dict):
                    raise ValueError("Dify event must be an object")
                yield event


def chat_result(chunks):
    messages, agent_messages = [], []
    replacement = None
    conversation = metadata = None
    waiting_for_workflow = finished_workflow = ended = False
    for event in _events(chunks):
        kind = event.get("event")
        session_id = event.get("conversation_id")
        if isinstance(session_id, str) and session_id:
            if conversation is not None and session_id != conversation:
                raise ValueError("Dify conversation changed inside response")
            conversation = session_id
        if kind in ("error", "workflow_paused", "human_input_required"):
            raise ValueError("Dify did not complete the turn")
        if kind in ("message", "agent_message", "message_replace"):
            answer = event.get("answer")
            if not isinstance(answer, str):
                raise ValueError("Dify event missing text")
            if kind == "message_replace":
                replacement = answer
            else:
                (agent_messages if kind == "agent_message" else messages).append(answer)
        elif kind == "message_end":
            metadata = event.get("metadata")
            ended = True
        elif kind == "workflow_started":
            waiting_for_workflow = True
        elif kind == "workflow_finished":
            result = event.get("data")
            if not isinstance(result, dict) or result.get("status") != "succeeded":
                raise ValueError("Dify workflow did not succeed")
            finished_workflow = True
        if ended and (not waiting_for_workflow or finished_workflow):
            if not (messages or agent_messages or replacement is not None):
                raise ValueError("Dify ended without a reply")
            # Native Agent can send a final message repeating the accumulated agent_message deltas.
            answer = replacement if replacement is not None else "".join(messages or agent_messages)
            return {"answer": answer, "conversation_id": conversation, "metadata": metadata}
    raise ValueError("Dify stream ended before a completion event")
