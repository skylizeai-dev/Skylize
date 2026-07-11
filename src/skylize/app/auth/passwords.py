"""Password hashing and verification.

Uses the `bcrypt` library directly instead of passlib to avoid the
passlib 1.7.x / bcrypt 4.x+ incompatibility (passlib's self-test uses a
72-byte probe that bcrypt ≥4.0 strictly rejects).
"""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())
