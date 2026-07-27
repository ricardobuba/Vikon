"""Autenticación mínima y autónoma (sin dependencias nuevas).

- Contraseñas: PBKDF2-HMAC-SHA256 con sal por usuario (nunca en claro).
- Sesión: token firmado con HMAC (`user_id.expiry.firma`) → cookie httponly.
  Sin estado en servidor; el secreto vive en la BD (`app_meta`).

Nota de seguridad: pensado para uso local/single-user. Para producción real
convendría subir iteraciones, rotar el secreto y usar cookies Secure sobre HTTPS.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

_ITERATIONS = 200_000
TOKEN_TTL_S = 30 * 24 * 3600           # 30 días
MIN_PASSWORD_LEN = 6


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Devuelve (hash_hex, salt_hex)."""
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return dk.hex(), salt.hex()


def verify_password(password: str, pw_hash: str, pw_salt: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(pw_salt), _ITERATIONS)
    return hmac.compare_digest(dk.hex(), pw_hash)


def make_token(user_id: int, secret: str, ttl: int = TOKEN_TTL_S) -> str:
    payload = f"{user_id}.{int(time.time()) + ttl}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def parse_token(token: str | None, secret: str) -> int | None:
    """Valida firma y expiración; devuelve el user_id o None."""
    if not token:
        return None
    try:
        uid, exp, sig = token.split(".")
        payload = f"{uid}.{exp}"
        good = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, sig):
            return None
        if int(exp) < int(time.time()):
            return None
        return int(uid)
    except (ValueError, AttributeError):
        return None
