"""Explicit role inheritance and the small browser surface for ordinary accounts."""

from typing import Literal

AccountRole = Literal["admin", "operator", "reviewer", "user"]


def permits(role, required):
    return role == required or (role == "admin" and required in {"operator", "reviewer"})


def basic_route(request):
    # Match the registered route, not a client-supplied path prefix or query.
    path = getattr(request.scope.get("route"), "path", None)
    method = request.method
    if method in {"GET", "HEAD"} and path in {
        "/api/v1/workbench/cases",
        "/api/v1/workbench/cases/{identifier}",
        "/api/v1/workbench/overview",
        "/api/v1/workbench/versions",
        "/api/v1/workbench/runs",
        "/api/v1/workbench/runs/{run_id}/reproduction",
        "/api/v1/fault-profiles",
        "/api/v1/external-agents",
        "/api/v1/external-agents/check",
        "/api/v1/external-agents/cases",
        "/api/v1/external-agents/trace-setup",
        "/api/v1/runs/{run_id}/snapshot",
        "/api/v1/runs/{run_id}/reliability",
        "/api/v1/runs/{run_id}/events",
        "/api/v1/runs/{run_id}/events/stream",
        "/api/v1/runs/{run_id}/events/{event_id}",
        "/api/v1/runs/{run_id}/artifacts/{artifact_id}",
        "/api/v1/executions/{execution_id}",
        "/api/v1/executions/{execution_id}/events",
        "/api/v1/executions/{execution_id}/assertions",
        "/api/v1/reports/runs/{identifier}",
        "/api/v1/reports/compare",
        "/api/v1/reports/fault-compare",
        "/api/v1/reports/exports/{artifact_id}/download",
    }:
        return True
    if method == "POST" and path in {
        "/api/v1/workbench/run-plans",
        "/api/v1/workbench/run-plans/commit",
        "/api/v1/external-agents/self-service/challenge",
        "/api/v1/external-agents/self-service/connect",
        "/api/v1/external-agents/register",
        "/api/v1/external-agents/cases",
        "/api/v1/runs/{run_id}/cancel",
        "/api/v1/runs/{run_id}/executions/{execution_id}/external-receipt",
        "/api/v1/runs/{run_id}/executions/{execution_id}/input",
        "/api/v1/reports/runs/{identifier}/exports",
    }:
        return True
    if not path:
        return False
    for base, sid in (
        ("/api/v1/interactive", "{session_id}"),
        ("/api/v1/refund-interactive", "{sid}"),
    ):
        record = base + "/sessions/" + sid
        if method in {"GET", "HEAD"} and path in {
            base + "/access",
            base + "/sessions",
            record,
            record + "/history",
            record + "/receipts/{request_id}",
        }:
            return True
        if method == "POST" and path in {
            base + "/sessions",
            record + "/messages",
            record + "/resume",
            record + "/retry",
            record + "/decision",
        }:
            return True
        if method == "DELETE" and path == record:
            return True
    return False
