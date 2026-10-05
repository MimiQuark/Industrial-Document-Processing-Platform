from uuid import uuid4

from app.security import create_access_token, decode_access_token, hash_password, verify_password


def test_password_hash_and_verify():
    hashed = hash_password("Example123!")
    assert hashed != "Example123!"
    assert verify_password("Example123!", hashed)
    assert not verify_password("wrong", hashed)


def test_jwt_round_trip():
    user_id = uuid4()
    token = create_access_token(user_id)
    assert decode_access_token(token) == user_id
