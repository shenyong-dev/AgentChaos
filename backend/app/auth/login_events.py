"""Bounded sign-in audits with offline IP location lookup."""

import ipaddress
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import maxminddb
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models import LoginEvent

log = logging.getLogger("agentchaos.login")
RETENTION_DAYS = 90
LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(block)
    for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)
PAGE_SIZE = 25
PRUNE_LIMIT = 1000


def address_of(value):
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        return address.ipv4_mapped or ipaddress.IPv6Address(int(address))
    return address


def location_of(address, path):
    if address is None:
        return None, None
    if address.is_loopback:
        return "本机", "local"
    if address.is_link_local or any(address in network for network in LOCAL_NETWORKS):
        return "内网", "local"
    if not address.is_global or address.is_multicast or not path:
        return None, None

    try:
        with maxminddb.open_database(PROJECT_ROOT / Path(path)) as reader:
            if reader.metadata().database_type != "DBIP-City-Lite":
                return None, None
            entry = reader.get(address)
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        return None, None
    if not isinstance(entry, dict):
        return None, None

    subdivisions = entry.get("subdivisions")
    parts = [
        entry.get("country"),
        *(subdivisions if isinstance(subdivisions, list) else []),
        entry.get("city"),
    ]
    labels = []
    for part in parts:
        names = part.get("names") if isinstance(part, dict) else None
        if not isinstance(names, dict):
            continue
        label = names.get("zh-CN") or names.get("en")
        if isinstance(label, str) and label and label not in labels:
            labels.append(label)
    place = " · ".join(labels)[:180]
    return (place, "dbip") if place else (None, None)


def record_login(engine, settings, request, *, outcome, reason=None, user=None):
    # The server supplies the peer, after its configured trusted proxy middleware.
    address = address_of(request.client.host if request.client else None)
    location, source = location_of(address, settings.login_geoip_db_path)
    raw_agent = request.headers.get("user-agent", "")[:384]
    user_agent = "".join(char for char in raw_agent if char.isprintable()).strip() or None
    request_id = getattr(request.state, "request_id", None)
    try:
        with Session(engine) as db, db.begin():
            db.add(
                LoginEvent(
                    user_id=user.id if user else None,
                    username=user.username if user else None,
                    role=user.role if user else None,
                    outcome=outcome,
                    reason=reason,
                    ip_address=str(address) if address else None,
                    location=location,
                    location_source=source,
                    user_agent=user_agent,
                    request_id=request_id,
                )
            )
    except SQLAlchemyError as exc:
        # Logging failure must not strand a session or leak SQL parameters.
        log.warning("Login event could not be saved (%s)", type(exc).__name__)


def list_login_events(engine, *, cursor, query, outcome, user_id, days):
    since = datetime.now(UTC) - timedelta(days=days)
    statement = select(LoginEvent).where(LoginEvent.created_at >= since)
    if cursor:
        statement = statement.where(LoginEvent.id < cursor)
    if user_id:
        statement = statement.where(LoginEvent.user_id == user_id)
    if outcome:
        statement = statement.where(LoginEvent.outcome == outcome)
    if query:
        address = address_of(query)
        statement = statement.where(
            or_(
                LoginEvent.username.contains(query.casefold(), autoescape=True),
                LoginEvent.ip_address == str(address)
                if address
                else LoginEvent.ip_address.startswith(query, autoescape=True),
            )
        )
    statement = statement.order_by(LoginEvent.id.desc()).limit(PAGE_SIZE + 1)
    with Session(engine) as db:
        rows = list(db.scalars(statement))
    fields = (
        "created_at",
        "username",
        "role",
        "outcome",
        "reason",
        "ip_address",
        "location",
        "location_source",
        "user_agent",
        "request_id",
    )
    return {
        "items": [
            {"id": str(row.id), **{field: getattr(row, field) for field in fields}}
            for row in rows[:PAGE_SIZE]
        ],
        "next_cursor": str(rows[PAGE_SIZE - 1].id) if len(rows) > PAGE_SIZE else None,
    }


def prune_login_events(engine):
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    with Session(engine) as db, db.begin():
        result = db.execute(
            delete(LoginEvent)
            .where(LoginEvent.created_at < cutoff)
            .with_dialect_options(mysql_limit=PRUNE_LIMIT)
        )
        return result.rowcount
