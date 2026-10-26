"""Backpressure deadline around native FastAPI SSE's bounded producer streams."""

import anyio


class SSESendDeadlineMiddleware:
    def __init__(self, app, timeout=5):
        self.app, self.timeout = app, timeout

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").endswith("/events/stream"):
            return await self.app(scope, receive, send)

        async def bounded_send(message):
            with anyio.fail_after(self.timeout):
                await send(message)

        try:
            await self.app(scope, receive, bounded_send)
        except TimeoutError:
            # No successful frame is claimed after a blocked send. The client resumes
            # from the last completely parsed ID; request task groups unwind normally.
            return
