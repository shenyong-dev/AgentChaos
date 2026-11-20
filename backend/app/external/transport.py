"""One fixed endpoint/IP, TLS verifies original hostname; no DNS, proxy, redirect or retry."""

import json
import ssl
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

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
    def __init__(self, entry, settings, *, owner=None, access_token=None):
        self.entry, self.settings = entry, settings
        self.owner, self._access_token = owner, access_token

    def call(self, request, *, deadline=None):
        decoded = self.json("POST", body=request, deadline=deadline)
        try:
            value = Envelope.model_validate(decoded)
            data = value.model_dump()
            for key in ("protocol", "revision", "operation", "request_id", "binding"):
                if data[key] != request[key]:
                    raise ValueError("Response binding mismatch")
            return value.data
        except ValueError:
            raise unknown() from None

    def json(
        self,
        method,
        *,
        body=None,
        path=None,
        query=None,
        extra_headers=None,
        deadline=None,
        response_limit=65536,
        event_reader=None,
    ):
        if not self.settings.external_agent_calls_enabled:
            raise AgentError("external_calls_disabled", "外部对象调用开关尚未开启。", 403)
        host, port, ip = address(self.entry, self.settings)
        endpoint = urlsplit(self.entry.endpoint)
        url = urlunsplit(
            (endpoint.scheme, endpoint.netloc, path or endpoint.path, urlencode(query or {}), "")
        )
        raw = (
            json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            if body is not None
            else b""
        )
        if len(raw) > 65536:
            raise AgentError("external_input_limit", "外部对象请求超过输入上限。", 400)
        deadline = min(deadline or float("inf"), time.monotonic() + self.entry.timeout_seconds)
        timeout = max(0.01, deadline - time.monotonic())
        token = self._access_token or credential(self.entry, self.settings, owner=self.owner)
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json" if event_reader else "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            **(extra_headers or {}),
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
                    method,
                    url,
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
                response_headers = {k.lower(): v for k, v in response.headers}
                media_type = (
                    response_headers.get(b"content-type", b"").split(b";")[0].strip().lower()
                )
                if response_headers.get(b"content-encoding", b"identity").lower() != b"identity":
                    raise ValueError("Response format not supported")
                chunks = bounded_chunks(response, deadline, response_limit)
                if media_type == b"text/event-stream" and event_reader:
                    decoded = event_reader(chunks)
                elif media_type == b"application/json":
                    decoded = decode_json(b"".join(chunks))
                else:
                    raise ValueError("Response format not supported")
            return without_credentials(decoded, (token,))
        except (
            httpcore.NetworkError,
            httpcore.TimeoutException,
            httpcore.ProtocolError,
            ValueError,
            OSError,
            RecursionError,
        ):
            # Never expose remote error bodies, endpoint, headers or provider exception text.
            raise unknown() from None


def bounded_chunks(response, deadline, limit):
    remaining = limit
    for chunk in response.iter_stream():
        remaining -= len(chunk)
        if time.monotonic() >= deadline or remaining < 0:
            raise ValueError("Response exceeds time/size limit")
        yield chunk


def decode_json(raw):
    return json.loads(raw, object_pairs_hook=unique_json, parse_constant=invalid_number)


def invalid_number(value):
    raise ValueError("Non-finite JSON number")


def without_credentials(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [without_credentials(item, secrets) for item in value]
    if isinstance(value, dict):
        return {
            without_credentials(k, secrets): without_credentials(v, secrets)
            for k, v in value.items()
        }
    return value


def unknown():
    return AgentError(
        "external_result_unknown", "外部调用未取得可信回执，保留原请求标识，请先查证。", 503
    )
