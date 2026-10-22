"""Token compatibility and rejection checks for the JWT library migration."""
import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import JWTError, create_token, decode_token


def test_previous_hs256_token_remains_valid():
    # Encode the prior wire format independently of the production JWT library.
    def segment(value):
        data = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    claims = {"sub": "existing-user", "type": "refresh", "jti": "session-id",
              "exp": int(time.time()) + 60}
    message = segment({"alg": "HS256", "typ": "JWT"}) + b"." + segment(claims)
    signature = hmac.new(get_settings().secret_key.encode(), message, hashlib.sha256).digest()
    token = (message + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()
    assert decode_token(token, expected_type="refresh") == claims


def test_access_token_preserves_subject_and_scope():
    token = create_token("existing-user", jti="session-id", scope="scenic")
    claims = decode_token(token, expected_type="access")
    assert (claims["sub"], claims["jti"], claims["scope"]) == (
        "existing-user", "session-id", "scenic",
    )


@pytest.mark.parametrize("kind", ["expired", "wrong-key", "wrong-algorithm", "unsigned", "wrong-type"])
def test_invalid_tokens_are_rejected(kind, monkeypatch):
    claims = {"sub": "existing-user", "type": "access", "exp": int(time.time()) + 60}
    key = get_settings().secret_key
    algorithm = "HS256"
    if kind == "expired":
        claims["exp"] = int(time.time()) - 60
    elif kind == "wrong-key":
        key = "different-test-signing-key-at-least-32-bytes"
    elif kind == "wrong-algorithm":
        monkeypatch.setattr(get_settings(), "algorithm", "HS384")
    elif kind == "unsigned":
        algorithm, key = "none", None
    elif kind == "wrong-type":
        claims["type"] = "refresh"
    token = jwt.encode(claims, key, algorithm=algorithm)
    with pytest.raises(JWTError):
        decode_token(token, expected_type="access")
