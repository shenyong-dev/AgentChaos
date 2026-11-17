"""Offline checks for conversational state and the standalone receipt contract."""

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from server import Store, fingerprint
from triage import reply


class SupportChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "support.sqlite3"
        self.store = Store(self.path)
        self.binding = {"tenant": "a" * 64, "scope": "test:standalone", "session_id": str(uuid4())}
        self.message("prepare", str(uuid4()), {})

    def message(self, operation, identifier, data, *, drop_on_text=None):
        return self.store.handle(
            {
                "binding": self.binding,
                "operation": operation,
                "request_id": identifier,
                "data": data,
            },
            drop_on_text,
        )

    @staticmethod
    def payload(text):
        return {
            "text": text,
            "expected_revision": 0,
            "limits": {
                "max_model_calls": 1,
                "max_tokens": 8192,
                "max_cost_microusd": 0,
                "timeout_seconds": 10,
            },
        }

    def test_switching_orders_rechecks_the_issue(self):
        initial, answer = reply({}, "SO-137416 收到时商品碎了")
        self.assertIn("商品破损", answer)
        switched, answer = reply(initial, "换查 SO-983267")
        self.assertNotIn("issue", switched)
        self.assertIn("请描述遇到的问题", answer)
        _, answer = reply(switched, "一直没收到")
        self.assertIn("SO-983267 建议归入物流查询", answer)

    def test_lost_response_can_be_looked_up_after_reopen(self):
        request_id = str(uuid4())
        payload = self.payload("SO-137416 收到时商品碎了")
        result, dropped = self.message("execute", request_id, payload, drop_on_text=payload["text"])
        self.assertTrue(dropped)
        self.store = Store(self.path, resume=True)
        lookup, _ = self.message(
            "lookup",
            request_id,
            {
                "original_operation": "execute",
                "input_hash": fingerprint(payload),
            },
        )
        self.assertEqual(lookup["result"], result)
        self.assertEqual(
            self.message("execute", request_id, payload, drop_on_text=payload["text"]),
            (result, False),
        )
        with self.assertRaises(ValueError):
            self.message("execute", request_id, self.payload("其他消息"))
        self.binding = {**self.binding, "session_id": str(uuid4())}
        lookup, _ = self.message(
            "lookup",
            request_id,
            {
                "original_operation": "execute",
                "input_hash": fingerprint(payload),
            },
        )
        self.assertEqual(lookup["status"], "not_found")

    def test_cancellation_before_processing_skips_agent(self):
        request_id = str(uuid4())
        payload = self.payload("SO-137416 收到时商品碎了")
        status, _ = self.message(
            "cancel",
            str(uuid4()),
            {
                "original_request_id": request_id,
                "input_hash": fingerprint(payload),
            },
        )
        self.assertEqual(status["status"], "confirmed")
        result, _ = self.message("execute", request_id, payload)
        self.assertEqual((result["state"], result["assistant_text"]), ("cancelled", ""))

    def test_invalid_request_limits_do_not_consume_revision(self):
        request_id = str(uuid4())
        payload = self.payload("请问怎么处理")
        with self.assertRaises(ValueError):
            self.message("execute", request_id, {**payload, "limits": {"max_model_calls": True}})
        result, _ = self.message("execute", request_id, payload)
        self.assertEqual(result["revision"], 1)


if __name__ == "__main__":
    unittest.main()
