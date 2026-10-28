"""Deterministic booking transactions; no LLM, Redis lock or implicit write retry."""

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import TypeVar
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.passwords import verify_password
from app.models import Booking, BookingDraft, BookingSlot, Room, User
from app.schemas.booking import (
    AvailabilityQuery,
    AvailabilityView,
    BookingToolResult,
    BookingView,
    CommitState,
    ConfirmBooking,
    DraftView,
    PreviewBooking,
    RoomView,
)

LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
DRAFT_TTL = timedelta(minutes=5)
SLOT = timedelta(minutes=30)
R = TypeVar("R")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class BookingContext:
    scope_id: str
    actor_key: str
    reference_clock: datetime
    session_id: str | None = None
    execution_id: int | None = None
    lease_token: str | None = None
    before_commit: object | None = None

    def __post_init__(self):
        if not re.fullmatch(r"demo|test:[A-Za-z0-9][A-Za-z0-9:_-]{0,58}", self.scope_id):
            raise ValueError("Invalid trusted scope")
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,64}", self.actor_key):
            raise ValueError("Invalid trusted actor")
        if self.reference_clock.utcoffset() is None:
            raise ValueError("Trusted reference clock must be aware")
        if self.session_id is not None:
            UUID(self.session_id)
        if (self.execution_id is None) != (self.lease_token is None):
            raise ValueError("Execution fencing requires both id and token")


class BookingError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 409,
        *,
        commit_state: CommitState = "not_committed",
        retryable: bool = False,
        request_key: UUID | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.commit_state = commit_state
        self.retryable = retryable
        self.request_key = request_key


def payload_hash(context: BookingContext, room_id: int, values: AvailabilityQuery) -> str:
    data = {
        "schema": "booking-v1",
        "scope": context.scope_id,
        "actor": context.actor_key,
        "room_id": room_id,
        "start_at": values.start_at.astimezone(UTC).isoformat(),
        "end_at": values.end_at.astimezone(UTC).isoformat(),
        "attendees": values.attendees,
    }
    if context.session_id is not None:
        data["session_id"] = context.session_id
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_times(context: BookingContext, values: AvailabilityQuery):
    start, end = values.start_at.astimezone(UTC), values.end_at.astimezone(UTC)
    if any(value.minute % 30 or value.second or value.microsecond for value in (start, end)):
        raise BookingError("time_alignment", "起止时间必须按30分钟对齐。", 400)
    if not SLOT <= end - start <= 4 * SLOT:
        raise BookingError("invalid_duration", "单次预约须为30至120分钟。", 400)
    if start < context.reference_clock or end > context.reference_clock + timedelta(days=7):
        raise BookingError("outside_booking_window", "预约须在参考时钟至其后7天内。", 400)


def validate_room(room: Room, values: AvailabilityQuery):
    if not room.enabled:
        raise BookingError("room_disabled", "场地当前不可预约。")
    if values.attendees > room.capacity:
        raise BookingError("capacity_exceeded", "人数超过场地容量。", 400)
    try:
        hours = room.opening_hours_json
        if hours["timezone"] != "Asia/Shanghai":
            raise ValueError("Unsupported opening timezone")
        opens, closes = time.fromisoformat(hours["opens"]), time.fromisoformat(hours["closes"])
        if opens.tzinfo is not None or closes.tzinfo is not None or opens >= closes:
            raise ValueError("Invalid opening hours")
    except (KeyError, TypeError, ValueError) as exc:
        raise BookingError("room_rules_invalid", "场地规则配置有误，无法判断可用性。", 503) from exc
    start, end = values.start_at.astimezone(LOCAL_ZONE), values.end_at.astimezone(LOCAL_ZONE)
    if start.date() != end.date() or start.time() < opens or end.time() > closes:
        raise BookingError("outside_opening_hours", "预约时段不在场地开放时间内。", 400)


class BookingService:
    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] = utc_now):
        self.engine = engine
        self.clock = clock

    def _now(self):
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("Wall clock must be aware")
        return now.astimezone(UTC)

    def _write(
        self,
        context: BookingContext,
        operation: Callable[[Session], R],
        *,
        request_key: UUID | None = None,
        booking_effect: bool = False,
    ) -> R:
        # Connection-level lock is released before returning this connection to the pool.
        # Scope + actor includes concurrent idempotency keys targeting different rooms.
        lock = (
            "ac:booking:"
            + hashlib.sha256(
                json.dumps([context.scope_id, context.actor_key]).encode()
            ).hexdigest()[:48]
        )
        try:
            with self.engine.connect() as connection:
                acquired = False
                try:
                    acquired = (
                        connection.scalar(text("SELECT GET_LOCK(:name, 3)"), {"name": lock}) == 1
                    )
                    connection.commit()
                    if not acquired:
                        raise BookingError(
                            "booking_busy",
                            "当前身份有其他预约操作，请稍后查证或重试。",
                            503,
                            commit_state="unknown" if booking_effect else "not_committed",
                            retryable=True,
                            request_key=request_key,
                        )
                    with Session(connection) as session, session.begin():
                        if context.execution_id is not None:
                            from app.executors.control import fence

                            fence(
                                session,
                                context.execution_id,
                                context.lease_token,
                                scope_id=context.scope_id,
                                session_id=context.session_id,
                            )
                        result = operation(session)
                        if context.before_commit is not None:
                            context.before_commit(session, result)
                        if context.execution_id is not None:
                            fence(
                                session,
                                context.execution_id,
                                context.lease_token,
                                scope_id=context.scope_id,
                                session_id=context.session_id,
                            )
                    return result
                finally:
                    if acquired:
                        if connection.in_transaction():
                            connection.rollback()
                        try:
                            released = connection.scalar(
                                text("SELECT RELEASE_LOCK(:name)"), {"name": lock}
                            )
                            connection.commit()
                            if released != 1:
                                connection.invalidate()
                        except DBAPIError:
                            # Do not mask a known successful commit due to lock-release I/O.
                            connection.invalidate()
        except BookingError as exc:
            if exc.request_key is None:
                exc.request_key = request_key
            raise
        except IntegrityError as exc:
            if exc.orig.args[0] == 1062:
                raise BookingError(
                    "booking_conflict",
                    "预约或幂等标识发生冲突，请查证原请求。",
                    request_key=request_key,
                ) from exc
            raise BookingError("booking_storage_error", "预约数据约束校验失败。", 503) from exc
        except DBAPIError as exc:
            raise BookingError(
                "booking_state_unknown" if booking_effect else "booking_unavailable",
                "数据库通信未完成；请按原请求标识查证，不要换键重复提交。",
                503,
                commit_state="unknown" if booking_effect else "not_committed",
                request_key=request_key,
            ) from exc

    @staticmethod
    def _busy(session: Session, context: BookingContext, room: Room, values: AvailabilityQuery):
        return (
            session.scalar(
                select(BookingSlot.id)
                .where(
                    BookingSlot.scope_id == context.scope_id,
                    BookingSlot.room_id == room.id,
                    BookingSlot.slot_start >= values.start_at,
                    BookingSlot.slot_start < values.end_at,
                )
                .limit(1)
            )
            is not None
        )

    def query_availability(self, context: BookingContext, values: AvailabilityQuery):
        validate_times(context, values)
        try:
            with Session(self.engine) as session:
                query = (
                    select(Room)
                    .where(
                        Room.scope_id == context.scope_id,
                        Room.enabled.is_(True),
                        Room.capacity >= values.attendees,
                    )
                    .order_by(Room.capacity, Room.room_code)
                    .limit(51)
                )
                if values.room_code:
                    query = query.where(Room.room_code == values.room_code)
                rooms = list(session.scalars(query))
                available = []
                for room in rooms[:50]:
                    try:
                        validate_room(room, values)
                    except BookingError as exc:
                        if exc.code == "outside_opening_hours":
                            continue
                        raise
                    if not self._busy(session, context, room, values):
                        available.append(RoomView(room_code=room.room_code, capacity=room.capacity))
                return BookingToolResult[AvailabilityView](
                    code="availability_checked",
                    summary="已查询当前空闲；提交时仍会重新校验。",
                    commit_state="not_applicable",
                    data=AvailabilityView(
                        rooms=available,
                        start_at=values.start_at,
                        end_at=values.end_at,
                        attendees=values.attendees,
                        truncated=len(rooms) > 50,
                    ),
                )
        except DBAPIError as exc:
            raise BookingError(
                "booking_unavailable",
                "数据库暂不可用，未得到可用性结论。",
                503,
                commit_state="not_applicable",
                retryable=True,
            ) from exc

    def preview_booking(self, context: BookingContext, values: PreviewBooking):
        def operation(session):
            session.execute(
                update(BookingDraft)
                .where(
                    BookingDraft.scope_id == context.scope_id,
                    BookingDraft.actor_key == context.actor_key,
                    BookingDraft.session_id == context.session_id,
                    BookingDraft.status == "pending",
                )
                .values(status="superseded")
            )
            try:
                validate_times(context, values)
                room = session.scalar(
                    select(Room)
                    .where(Room.scope_id == context.scope_id, Room.room_code == values.room_code)
                    .with_for_update()
                )
                if room is None:
                    raise BookingError("room_not_found", "场地不存在。", 404)
                validate_room(room, values)
                if self._busy(session, context, room, values):
                    raise BookingError("slot_conflict", "预约时段已被占用。")
            except BookingError as exc:
                # A changed request revokes old confirmation even if its replacement
                # is invalid. Commit that revocation before returning the error.
                return exc
            draft = BookingDraft(
                draft_uuid=str(uuid4()),
                scope_id=context.scope_id,
                actor_key=context.actor_key,
                session_id=context.session_id,
                room_id=room.id,
                start_at=values.start_at,
                end_at=values.end_at,
                attendees=values.attendees,
                payload_hash=payload_hash(context, room.id, values),
                status="pending",
                expires_at=self._now() + DRAFT_TTL,
            )
            session.add(draft)
            session.flush()
            view = DraftView(
                draft_id=draft.draft_uuid,
                draft_hash=draft.payload_hash,
                suggested_idempotency_key=draft.draft_uuid,
                room_code=room.room_code,
                start_at=draft.start_at,
                end_at=draft.end_at,
                attendees=draft.attendees,
                status=draft.status,
                expires_at=draft.expires_at,
            )
            return BookingToolResult[DraftView](
                code="confirmation_required",
                summary="请展示完整草稿并等待用户明确确认。",
                data=view,
                commit_state="not_committed",
                request_key=view.suggested_idempotency_key,
                evidence_refs=[f"draft:{draft.draft_uuid}"],
            )

        result = self._write(context, operation)
        if isinstance(result, BookingError):
            raise result
        return result

    @staticmethod
    def _booking_view(session: Session, row: Booking) -> BookingView:
        room = session.get(Room, row.room_id)
        draft = session.get(BookingDraft, row.draft_id)
        return BookingView(
            booking_id=row.booking_uuid,
            draft_id=draft.draft_uuid,
            idempotency_key=row.idempotency_key,
            room_code=room.room_code,
            start_at=row.start_at,
            end_at=row.end_at,
            attendees=row.attendees,
            status=row.status,
            created_at=row.created_at,
        )

    def _booking_result(self, session, row, code="booking_confirmed"):
        return BookingToolResult[BookingView](
            code=code,
            summary="已查到原预约事实。",
            data=self._booking_view(session, row),
            request_key=row.idempotency_key,
            commit_state="committed",
            evidence_refs=[f"booking:{row.booking_uuid}"],
        )

    def confirm_booking(self, context: BookingContext, values: ConfirmBooking):
        if not values.confirmed:
            raise BookingError("confirmation_required", "尚未收到明确确认，未提交预约。", 400)

        def operation(session):
            previous = session.scalar(
                select(Booking).where(
                    Booking.scope_id == context.scope_id,
                    Booking.actor_key == context.actor_key,
                    Booking.idempotency_key == str(values.idempotency_key),
                )
            )
            if previous is not None:
                original = session.get(BookingDraft, previous.draft_id)
                if original.session_id != context.session_id:
                    raise BookingError("draft_not_found", "当前会话找不到该确认草稿。", 404)
                if original.draft_uuid != str(values.draft_id) or not hmac.compare_digest(
                    previous.payload_hash, values.draft_hash
                ):
                    raise BookingError("idempotency_conflict", "同一幂等标识不能用于不同预约参数。")
                return self._booking_result(session, previous, "booking_replayed")
            draft = session.scalar(
                select(BookingDraft)
                .where(
                    BookingDraft.scope_id == context.scope_id,
                    BookingDraft.actor_key == context.actor_key,
                    BookingDraft.session_id == context.session_id,
                    BookingDraft.draft_uuid == str(values.draft_id),
                )
                .with_for_update()
            )
            if draft is None:
                raise BookingError("draft_not_found", "当前身份下找不到该确认草稿。", 404)
            if draft.status != "pending":
                raise BookingError("draft_not_pending", "草稿已失效或已使用，须查看当前状态。")
            if self._now() >= draft.expires_at:
                raise BookingError("draft_expired", "确认草稿已过期，请重新预览。", 410)
            request = AvailabilityQuery(
                start_at=draft.start_at,
                end_at=draft.end_at,
                attendees=draft.attendees,
            )
            expected_hash = payload_hash(context, draft.room_id, request)
            if not hmac.compare_digest(
                draft.payload_hash, values.draft_hash
            ) or not hmac.compare_digest(expected_hash, draft.payload_hash):
                raise BookingError("draft_mismatch", "确认内容与当前草稿不一致。")
            validate_times(context, request)
            room = session.scalar(
                select(Room)
                .where(
                    Room.id == draft.room_id,
                    Room.scope_id == context.scope_id,
                )
                .with_for_update()
            )
            validate_room(room, request)
            # Other cooperating writers lock this room. The unique slot key also
            # rejects a stale snapshot or external writer at flush/commit.
            if self._busy(session, context, room, request):
                raise BookingError("slot_conflict", "预览后时段已被占用，未提交预约。")
            row = Booking(
                booking_uuid=str(uuid4()),
                scope_id=context.scope_id,
                actor_key=context.actor_key,
                room_id=room.id,
                draft_id=draft.id,
                start_at=draft.start_at,
                end_at=draft.end_at,
                attendees=draft.attendees,
                idempotency_key=str(values.idempotency_key),
                payload_hash=draft.payload_hash,
                status="confirmed",
            )
            session.add(row)
            session.flush()
            self._write_slots(session, context, row)
            draft.status, draft.confirmed_at = "confirmed", self._now()
            session.flush()
            return self._booking_result(session, row)

        return self._write(
            context, operation, request_key=values.idempotency_key, booking_effect=True
        )

    @staticmethod
    def _write_slots(session: Session, context: BookingContext, row: Booking):
        slot = row.start_at
        while slot < row.end_at:
            session.add(
                BookingSlot(
                    scope_id=context.scope_id,
                    room_id=row.room_id,
                    slot_start=slot,
                    booking_id=row.id,
                )
            )
            session.flush()
            slot += SLOT

    def lookup_booking_by_request(self, context: BookingContext, request_key: UUID):
        try:
            with Session(self.engine) as session:
                row = session.scalar(
                    select(Booking).where(
                        Booking.scope_id == context.scope_id,
                        Booking.actor_key == context.actor_key,
                        Booking.idempotency_key == str(request_key),
                    )
                )
                if row is None:
                    return BookingToolResult[BookingView | None](
                        status="not_found",
                        code="request_not_found",
                        summary="本次查询未见已提交记录；在途事务仍可能随后提交，请保留原标识。",
                        data=None,
                        request_key=request_key,
                        commit_state="unknown",
                    )
                return self._booking_result(session, row, "request_found")
        except DBAPIError as exc:
            raise BookingError(
                "lookup_unavailable",
                "查证未完成，不能据此判断预约是否提交。",
                503,
                commit_state="unknown",
                retryable=True,
                request_key=request_key,
            ) from exc

    def cancel_booking(self, context: BookingContext, booking_id: UUID):
        def operation(session):
            row = session.scalar(
                select(Booking)
                .where(
                    Booking.booking_uuid == str(booking_id),
                    Booking.scope_id == context.scope_id,
                    Booking.actor_key == context.actor_key,
                )
                .with_for_update()
            )
            if row is None:
                raise BookingError("booking_not_found", "当前身份下找不到该预约。", 404)
            session.execute(select(Room.id).where(Room.id == row.room_id).with_for_update()).all()
            if row.status != "cancelled":
                session.execute(
                    delete(BookingSlot).where(
                        BookingSlot.scope_id == context.scope_id,
                        BookingSlot.booking_id == row.id,
                    )
                )
                row.status = "cancelled"
                session.flush()
            return self._booking_result(session, row, "booking_cancelled")

        return self._write(context, operation, booking_effect=True)

    def revoke_pending_drafts(self, context: BookingContext, *, rejected=False):
        def operation(session):
            result = session.execute(
                update(BookingDraft)
                .where(
                    BookingDraft.scope_id == context.scope_id,
                    BookingDraft.actor_key == context.actor_key,
                    BookingDraft.session_id == context.session_id,
                    BookingDraft.status == "pending",
                )
                .values(status="rejected" if rejected else "superseded")
            )
            return result.rowcount

        return self._write(context, operation)

    def authenticate(self, username: str, password: str) -> int:
        try:
            with Session(self.engine) as session:
                user = session.scalar(select(User).where(User.username == username))
                if (
                    user is None
                    or not verify_password(password, user.password_hash)
                    or not user.enabled
                ):
                    raise BookingError("authentication_required", "账号或认证信息无效。", 401)
                if user.role != "operator":
                    raise BookingError("booking_forbidden", "当前角色无权执行预约操作。", 403)
                return user.id
        except DBAPIError as exc:
            raise BookingError("authentication_unavailable", "认证服务暂不可用。", 503) from exc
