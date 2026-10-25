from datetime import datetime

import pytest

from app.core.passwords import hash_password, verify_password
from app.models.base import UTCDateTime, snapshot


def test_salted_passwords_verify_without_storing_plaintext():
    password = "synthetic-unit-test-password"
    one, two = hash_password(password), hash_password(password)
    assert one != two
    assert password not in one
    assert verify_password(password, one)
    assert not verify_password("wrong", one)
    assert not verify_password(password, "malformed")


def test_snapshot_and_time_reject_ambiguous_or_invalid_values():
    with pytest.raises(ValueError):
        snapshot({"not_json": float("nan")})
    with pytest.raises(ValueError, match="aware"):
        UTCDateTime().process_bind_param(datetime(2026, 1, 1), None)
