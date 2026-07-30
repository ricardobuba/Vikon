"""Multi-perfil: el `state` del OAuth ata la cuenta de Strava al perfil correcto.

Es la pieza de seguridad nueva. Si el `state` fuera falsificable, alguien podría
enchufar una cuenta de Strava al perfil equivocado — y entonces dos personas
compartirían entrenamientos, que es justo lo que el multi-perfil debe impedir.
"""

from __future__ import annotations

import time

from cycling_coach.adapters.strava.oauth import build_authorize_url
from cycling_coach.auth import make_token, parse_token

SECRET = "secreto-de-prueba"


def test_state_recovers_the_athlete_it_was_issued_for():
    state = make_token(42, SECRET, ttl=600)
    assert parse_token(state, SECRET) == 42


def test_state_signed_with_another_secret_is_rejected():
    """Sin esto, cualquiera que conozca el formato podría fabricar un state."""
    forged = make_token(42, "otro-secreto", ttl=600)
    assert parse_token(forged, SECRET) is None


def test_tampering_with_the_athlete_id_invalidates_the_signature():
    state = make_token(1, SECRET, ttl=600)
    _, exp, sig = state.split(".")
    assert parse_token(f"2.{exp}.{sig}", SECRET) is None


def test_expired_state_is_rejected():
    """El state vive poco a propósito: un enlace viejo no debe seguir enlazando."""
    stale = make_token(7, SECRET, ttl=-1)
    assert parse_token(stale, SECRET) is None


def test_missing_state_is_rejected():
    assert parse_token(None, SECRET) is None
    assert parse_token("", SECRET) is None
    assert parse_token("basura", SECRET) is None


def test_two_profiles_get_distinguishable_states():
    a, b = make_token(1, SECRET, ttl=600), make_token(2, SECRET, ttl=600)
    assert parse_token(a, SECRET) == 1
    assert parse_token(b, SECRET) == 2
    assert a != b


def test_state_still_valid_just_before_expiry():
    state = make_token(3, SECRET, ttl=5)
    assert parse_token(state, SECRET) == 3
    assert int(state.split(".")[1]) > int(time.time())


def test_authorize_url_carries_the_callback_of_this_host():
    """El callback vuelve al MISMO host por el que entró el usuario: localhost
    desde el PC, la IP de la LAN desde el móvil."""
    url = build_authorize_url("123", "http://192.168.1.130:8730/api/strava/callback")
    assert "client_id=123" in url
    assert "192.168.1.130" in url and "%2Fapi%2Fstrava%2Fcallback" in url
    assert "activity%3Aread_all" in url        # hace falta para leer privadas
