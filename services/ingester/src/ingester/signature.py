import hashlib
import hmac

PREFIX = "sha256="


def sign(secret: bytes, body: bytes) -> str:
    """Return the `X-Hub-Signature-256` header value GitHub would send for `body`."""
    return PREFIX + hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, body: bytes, header: str | None) -> bool:
    """Check a GitHub `X-Hub-Signature-256` header against the raw request body."""
    if not header or not header.startswith(PREFIX):
        return False
    # Compare as bytes in constant time; str inputs to compare_digest must be ASCII.
    return hmac.compare_digest(sign(secret, body).encode(), header.encode())
