"""Portable salted hashes for local seed accounts; no authentication endpoint yet."""

import base64
import hashlib
import hmac
import secrets

ITERATIONS = 600_000


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Seed password must be supplied in the local environment")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(ITERATIONS),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        count = int(iterations)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= count <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt, validate=True), count
        )
        return hmac.compare_digest(actual, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError):
        return False
