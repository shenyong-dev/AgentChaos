"""One fixed endpoint/IP, TLS verifies original hostname; no DNS, proxy, redirect or retry."""

import json
import ssl
import time

import httpcore

from app.agents.errors import AgentError
from app.external.registry import address, credential
from app.external.schema import Envelope, unique_json


class DeadlineStream(httpcore.NetworkStream):
    def __init__(self, stream, deadline):
        self.stream, self.deadline = stream, deadline

    def remaining(self, timeout):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise httpcore.ReadTimeout("External request deadline reached")
        return min(left, timeout) if timeout is not None else left

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, timeout=self.remaining(timeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, timeout=self.remaining(timeout))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return DeadlineStream(
            self.stream.start_tls(ssl_context, server_hostname, self.remaining(timeout)),
            self.deadline,
        )

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class PinnedBackend(httpcore.NetworkBackend):
    def __init__(self, hostname, port, ip, deadline):
        self.hostname, self.port, self.ip, self.deadline = hostname, port, ip, deadline
        self.backend = httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self.hostname or port != self.port:
            raise httpcore.ConnectError("Unregistered origin")
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise httpcore.ConnectTimeout("External request deadline reached")
        stream = self.backend.connect_tcp(
            self.ip, port, min(timeout or left, left), local_address, socket_options
        )
        return DeadlineStream(stream, self.deadline)


class Client:
    def __init__(self, entry, settings):
        self.entry, self.settings = entry, settings

    def call(self, request, *, deadline=None):
        if not self.settings.external_agent_calls_enabled:
            raise AgentError("external_calls_disabled", "外部对象调用开关尚未开启。", 403)
        host, port, ip = address(self.entry, self.settings)
        raw = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
        if len(raw) > 65536:
            raise AgentError("external_input_limit", "外部对象请求超过输入上限。", 400)
        deadline = min(deadline or float("inf"), time.monotonic() + self.entry.timeout_seconds)
        timeout = max(0.01, deadline - time.monotonic())
        token = credential(self.entry)
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        try:
            with (
                httpcore.ConnectionPool(
                    ssl_context=ssl.create_default_context(),
                    retries=0,
                    network_backend=PinnedBackend(host, port, ip, deadline),
                    max_connections=1,
                    max_keepalive_connections=0,
                ) as pool,
                pool.stream(
                    "POST",
                    self.entry.endpoint,
                    headers=headers,
                    content=raw,
                    extensions={
                        "timeout": {
                            "connect": timeout,
                            "read": timeout,
                            "write": timeout,
                            "pool": timeout,
                        }
                    },
                ) as response,
            ):
                if response.status != 200:
                    raise ValueError("Invalid HTTP response")
                content = bytearray()
                response_headers = {k.lower(): v for k, v in response.headers}
                if (
                    response_headers.get(b"content-type", b"").split(b";")[0] != b"application/json"
                    or response_headers.get(b"content-encoding", b"identity") != b"identity"
                ):
                    raise ValueError("Response format not supported")
                for chunk in response.iter_stream():
                    if time.monotonic() >= deadline or len(content) + len(chunk) > 65536:
                        raise ValueError("Response exceeds time/size limit")
                    content.extend(chunk)
            decoded = json.loads(content, object_pairs_hook=unique_json)

            def without_credential(value):
                if isinstance(value, str):
                    return value.replace(token, "[REDACTED]")
                if isinstance(value, list):
                    return [without_credential(item) for item in value]
                if isinstance(value, dict):
                    return {without_credential(k): without_credential(v) for k, v in value.items()}
                return value

            value = Envelope.model_validate(without_credential(decoded))
            for key in ("protocol", "revision", "operation", "request_id", "binding"):
                if value.model_dump()[key] != request[key]:
                    raise ValueError("Response binding mismatch")
            return value.data
        except (
            httpcore.NetworkError,
            httpcore.TimeoutException,
            httpcore.ProtocolError,
            ValueError,
            OSError,
            RecursionError,
        ):
            # Never expose remote error bodies, endpoint, headers or provider exception text.
            raise AgentError(
                "external_result_unknown", "外部调用未取得可信回执，保留原请求标识，请先查证。", 503
            ) from None
