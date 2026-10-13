"""Ed25519 身份、签名与验证。"""
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class Identity:
    def __init__(self, private_key: Ed25519PrivateKey | None = None):
        self.private_key: Ed25519PrivateKey | None = private_key or Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()

    @property
    def address(self) -> str:
        import hashlib
        return hashlib.sha256(self.public_key.public_bytes_raw()).hexdigest()[:40]

    def sign(self, message: bytes) -> str:
        if self.private_key is None:
            raise ValueError("private key is not available")
        return b64(self.private_key.sign(message))

    def verify(self, message: bytes, signature: str) -> bool:
        try:
            self.public_key.verify(unb64(signature), message)
            return True
        except Exception:
            return False

    def export_public_key(self) -> str:
        return b64(self.public_key.public_bytes_raw())

    def export_private_key(self) -> str:
        if self.private_key is None:
            raise ValueError("private key is not available")
        return b64(self.private_key.private_bytes_raw())

    @classmethod
    def from_private_key(cls, value: str) -> "Identity":
        return cls(Ed25519PrivateKey.from_private_bytes(unb64(value)))

    @classmethod
    def from_public_key(cls, value: str) -> "Identity":
        obj = cls.__new__(cls)
        obj.private_key = None
        obj.public_key = Ed25519PublicKey.from_public_bytes(unb64(value))
        return obj
