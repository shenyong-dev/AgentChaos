"""Whitelisted target adapters, never dynamic code or URLs supplied by a client."""

from typing import Protocol


class TargetAdapter(Protocol):
    kind: str

    def manifest(self) -> dict: ...
    def prepare(self, scope: str, owner: int) -> None: ...
    def snapshot(self, scope: str, owner: int) -> list | None: ...


def adapter_for(engine, settings, kind):
    if kind == "reference_refund":
        from app.adapters.refund import RefundAdapter

        return RefundAdapter(engine, settings)
    raise ValueError("Unsupported target adapter")


def kind(manifest):
    return manifest.get("adapter_type", "langgraph_booking")
