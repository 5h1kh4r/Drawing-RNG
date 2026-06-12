from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Dict


def new_public_salt(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)


def derive_seed(seed_material: str, salt: str, domain: str = "drawing-rng", out_bytes: int = 32) -> bytes:
    """Demo seed derivation using keyed BLAKE2b.

    This is for the research prototype. It is not a production password manager.
    """
    key = hashlib.sha256((salt or "").encode("utf-8")).digest()
    msg = f"Drawing-RNG-v1|domain={domain}|{seed_material}".encode("utf-8")
    return hashlib.blake2b(msg, key=key, digest_size=out_bytes).digest()


def seed_hex(seed_material: str, salt: str, domain: str = "drawing-rng") -> str:
    return derive_seed(seed_material, salt, domain).hex()


def demo_password(seed_material: str, salt: str, domain: str, length: int = 18) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*()-_=+"
    seed = derive_seed(seed_material, salt, f"password:{domain}", out_bytes=64)
    return "".join(alphabet[b % len(alphabet)] for b in seed[:length])


def avatar_palette(seed_material: str, salt: str) -> Dict[str, str]:
    raw = derive_seed(seed_material, salt, "avatar", out_bytes=9)
    return {
        "primary": f"#{raw[0]:02x}{raw[1]:02x}{raw[2]:02x}",
        "secondary": f"#{raw[3]:02x}{raw[4]:02x}{raw[5]:02x}",
        "accent": f"#{raw[6]:02x}{raw[7]:02x}{raw[8]:02x}",
    }
