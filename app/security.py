from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    return _password_hash.verify(password, stored_hash)