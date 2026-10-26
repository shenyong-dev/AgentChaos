"""Compatibility boundary for the locked RedisSaver 0.5.2 interrupt read path."""

from langgraph.checkpoint.redis import RedisSaver


class SessionRedisSaver(RedisSaver):
    def _load_pending_writes_with_registry_check(
        self, thread_id, checkpoint_ns, checkpoint_id, checkpoint_has_writes, registry_has_writes
    ):
        # Integration tests observed durable interrupt writes while has_writes was
        # false. The parent fast path then skipped them. Consult the actual registry
        # (and parent's search fallback), not this advisory flag, for session resume.
        return super()._load_pending_writes_with_registry_check(
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            checkpoint_has_writes=True,
            registry_has_writes=registry_has_writes,
        )


class FencedRedisSaver(SessionRedisSaver):
    """A stale task cannot overwrite a newer attempt's checkpoint/pending writes."""

    def __init__(self, *args, control, **kwargs):
        self.control = control
        super().__init__(*args, **kwargs)

    def put(self, *args, **kwargs):
        with self.control.transaction():
            return super().put(*args, **kwargs)

    def put_writes(self, *args, **kwargs):
        with self.control.transaction():
            return super().put_writes(*args, **kwargs)
