"""Sealed messages over a public channel: X25519 + HKDF + ChaCha20-Poly1305, signed with Ed25519.

A member holds one key file with an encryption key (to read their review box) and a signing key (to prove
who sends a command). The coordinator holds the same kind of key; its public half is in anyp5.json.
"""
import base64
import json
import os
import zlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "anyp5-sealed-v1:"
RAW = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def b64(data):
    return base64.b64encode(data).decode()


def unb64(text):
    return base64.b64decode(text)


def keygen():
    box, sign = X25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    raw = dict(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
               encryption_algorithm=serialization.NoEncryption())
    return {"box": b64(box.private_bytes(**raw)), "sign": b64(sign.private_bytes(**raw))}


def public(key):
    box = X25519PrivateKey.from_private_bytes(unb64(key["box"])).public_key()
    sign = Ed25519PrivateKey.from_private_bytes(unb64(key["sign"])).public_key()
    return {"box": b64(box.public_bytes(**RAW)), "sign": b64(sign.public_bytes(**RAW))}


def _cipher(shared, eph, recipient):
    k = HKDF(hashes.SHA256(), 32, salt=None, info=b"anyp5" + eph + recipient).derive(shared)
    return ChaCha20Poly1305(k)


def seal(box_pub, data):
    """Encrypt bytes to a member's public box key. Only that member can open it."""
    recipient = unb64(box_pub)
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(**RAW)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(recipient))
    nonce = os.urandom(12)
    body = _cipher(shared, eph_pub, recipient).encrypt(nonce, zlib.compress(data, 9), None)
    return PREFIX + b64(eph_pub + nonce + body)


def unseal(key, text):
    raw = unb64(text.strip()[len(PREFIX):])
    eph_pub, nonce, body = raw[:32], raw[32:44], raw[44:]
    priv = X25519PrivateKey.from_private_bytes(unb64(key["box"]))
    recipient = priv.public_key().public_bytes(**RAW)
    shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
    return zlib.decompress(_cipher(shared, eph_pub, recipient).decrypt(nonce, body, None))


def is_sealed(text):
    return text.strip().startswith(PREFIX)


def sign(key, payload):
    """Signed command: {"payload": ..., "sig": ...}. The signature covers the canonical JSON payload."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = Ed25519PrivateKey.from_private_bytes(unb64(key["sign"])).sign(body)
    return {"payload": payload, "sig": b64(sig)}


def verify(sign_pub, message):
    body = json.dumps(message["payload"], sort_keys=True, separators=(",", ":")).encode()
    try:
        Ed25519PublicKey.from_public_bytes(unb64(sign_pub)).verify(unb64(message["sig"]), body)
        return True
    except Exception:
        return False
