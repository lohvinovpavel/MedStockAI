"""A throwaway RSA keypair for the whole test session. The token contract
between auth and the other six is only meaningful if a real signature is
verified with a real public key, so these tests mint and verify for real."""

import pathlib
import sys

# MUST come before `from app...` anywhere in this package. Seven services each
# install a top-level package named `app` into one venv, and analogue wins the
# name by alphabetical order — so without this, `from app.main import app`
# silently imports analogue's application and every test below is meaningless.
# See Defect C at the top of docs/auth-implementation.md.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from medstock_shared import settings  # noqa: E402


def test_conftest_resolved_the_right_app() -> None:
    """Guard for the line above. If this fails, every other test in this
    directory is testing some other service."""
    from app.main import app

    assert app.title == "auth"


@pytest.fixture(scope="session", autouse=True)
def jwt_keys() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.jwt_private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    settings.jwt_public_key = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
