"""SSH 크리덴셜 암호화/복호화 (Fernet)."""
import base64
import hashlib
from cryptography.fernet import Fernet

try:
    from .config import get_settings
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings


def _get_fernet() -> Fernet:
    key = get_settings().secret_key.encode()
    # SECRET_KEY → 32 bytes → base64 URL-safe (Fernet 키 형식)
    derived = hashlib.sha256(key).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
