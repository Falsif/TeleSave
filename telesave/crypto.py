"""Шифрование архива.

* Мастер-ключ AES-256 создаётся один раз и шифрует всё содержимое архива
  (тексты, имена, аватары, сессию Telegram, вложения).
* Сам мастер-ключ хранится в keystore.json в зашифрованном виде:
  - без пароля — через Windows DPAPI (ключ привязан к вашей учётной записи Windows);
  - с паролем — через scrypt + AES-GCM (без пароля архив не открыть).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import struct
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

log = logging.getLogger("telesave.crypto")


class WrongPassword(Exception):
    pass


class KeyUnavailable(Exception):
    pass


SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
_KEY_AAD = b"TeleSave-master-key"


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def derive_key(password: str, salt: bytes, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def wrap_with_password(master: bytes, password: str) -> dict:
    salt, nonce = os.urandom(16), os.urandom(12)
    key = derive_key(password, salt)
    ct = AESGCM(key).encrypt(nonce, master, _KEY_AAD)
    return {"kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
            "salt": _b64e(salt), "nonce": _b64e(nonce), "key": _b64e(ct)}


def unwrap_with_password(d: dict, password: str) -> bytes:
    key = derive_key(password, _b64d(d["salt"]), d.get("n", SCRYPT_N), d.get("r", SCRYPT_R), d.get("p", SCRYPT_P))
    try:
        return AESGCM(key).decrypt(_b64d(d["nonce"]), _b64d(d["key"]), _KEY_AAD)
    except InvalidTag:
        raise WrongPassword() from None


# ---------------------------------------------------------------- DPAPI ----
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _ENTROPY = b"TeleSave.dpapi.v1"

    def _to_blob(data: bytes):
        buf = (ctypes.c_ubyte * max(len(data), 1)).from_buffer_copy(data or b"\0")
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf

    def _call(fn, data: bytes, *mid) -> bytes:
        inp, _b1 = _to_blob(data)
        ent, _b2 = _to_blob(_ENTROPY)
        out = _BLOB()
        if not fn(ctypes.byref(inp), *mid, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def dpapi_protect(data: bytes) -> bytes:
        return _call(_crypt32.CryptProtectData, data, "TeleSave")

    def dpapi_unprotect(data: bytes) -> bytes:
        return _call(_crypt32.CryptUnprotectData, data, None)

    DPAPI_AVAILABLE = True
else:  # режим разработки на Linux/macOS — НЕ защищает ключ
    def dpapi_protect(data: bytes) -> bytes:
        log.warning("DPAPI недоступен (не Windows): ключ хранится без защиты ОС")
        return b"NODPAPI" + data

    def dpapi_unprotect(data: bytes) -> bytes:
        if not data.startswith(b"NODPAPI"):
            raise KeyUnavailable("ключ создан на Windows")
        return data[7:]

    DPAPI_AVAILABLE = False


class KeyStore:
    FILENAME = "keystore.json"

    def __init__(self, data_dir: Path | str):
        self.path = Path(data_dir) / self.FILENAME

    def exists(self) -> bool:
        return self.path.exists()

    def _load(self) -> dict:
        return json.loads(self.path.read_text("utf-8"))

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), "utf-8")
        os.replace(tmp, self.path)

    @property
    def mode(self) -> str:
        return self._load().get("mode", "dpapi")

    def has_password(self) -> bool:
        return self.mode == "password"

    def create(self, password: str | None = None) -> bytes:
        master = AESGCM.generate_key(bit_length=256)
        self.protect(master, password)
        return master

    def protect(self, master: bytes, password: str | None = None) -> None:
        if password:
            d = {"version": 1, "mode": "password", **wrap_with_password(master, password)}
        else:
            d = {"version": 1, "mode": "dpapi", "blob": _b64e(dpapi_protect(master))}
        self._save(d)

    def unlock(self, password: str | None = None) -> bytes:
        d = self._load()
        if d.get("mode") == "password":
            if not password:
                raise WrongPassword()
            return unwrap_with_password(d, password)
        try:
            return dpapi_unprotect(_b64d(d["blob"]))
        except Exception as e:
            raise KeyUnavailable(str(e)) from None


class Cipher:
    """AES-256-GCM для полей БД и потоковое шифрование файлов."""
    CHUNK = 1 << 20
    MAGIC = b"TSF1"

    def __init__(self, key: bytes):
        self._aes = AESGCM(key)

    # --- поля ---
    def enc(self, data) -> bytes | None:
        if data is None:
            return None
        if isinstance(data, str):
            data = data.encode("utf-8")
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, data, None)

    def dec(self, blob) -> bytes | None:
        if blob is None:
            return None
        blob = bytes(blob)
        return self._aes.decrypt(blob[:12], blob[12:], None)

    def dec_str(self, blob) -> str | None:
        d = self.dec(blob)
        return None if d is None else d.decode("utf-8", "replace")

    def enc_json(self, obj) -> bytes | None:
        return None if obj is None else self.enc(json.dumps(obj, ensure_ascii=False))

    def dec_json(self, blob):
        s = self.dec_str(blob)
        return json.loads(s) if s else {}

    # --- файлы ---
    def _encrypt_reader(self, read, out) -> None:
        out.write(self.MAGIC)
        idx, cur = 0, read(self.CHUNK)
        while True:
            nxt = read(self.CHUNK) if len(cur) == self.CHUNK else b""
            last = 0 if nxt else 1
            nonce = os.urandom(12)
            ct = self._aes.encrypt(nonce, cur, struct.pack(">QB", idx, last))
            out.write(struct.pack(">I", len(ct)))
            out.write(nonce)
            out.write(ct)
            if last:
                break
            cur, idx = nxt, idx + 1

    def encrypt_file(self, src, dst) -> None:
        dst = Path(dst)
        tmp = dst.with_suffix(dst.suffix + ".part")
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            self._encrypt_reader(fi.read, fo)
        os.replace(tmp, dst)

    def encrypt_bytes_to_file(self, data: bytes, dst) -> None:
        dst = Path(dst)
        tmp = dst.with_suffix(dst.suffix + ".part")
        with open(tmp, "wb") as fo:
            self._encrypt_reader(io.BytesIO(data).read, fo)
        os.replace(tmp, dst)

    def iter_decrypt(self, src):
        with open(src, "rb") as f:
            if f.read(4) != self.MAGIC:
                raise ValueError("Повреждённый файл архива")
            idx = 0
            while True:
                hdr = f.read(4)
                if len(hdr) < 4:
                    raise ValueError("Файл архива обрезан")
                ln = struct.unpack(">I", hdr)[0]
                nonce, ct = f.read(12), f.read(ln)
                pos = f.tell()
                more = f.read(1)
                f.seek(pos)
                last = 0 if more else 1
                yield self._aes.decrypt(nonce, ct, struct.pack(">QB", idx, last))
                if last:
                    return
                idx += 1

    def decrypt_file_to_bytes(self, src) -> bytes:
        return b"".join(self.iter_decrypt(src))

    def decrypt_file(self, src, dst) -> None:
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        with open(tmp, "wb") as fo:
            for chunk in self.iter_decrypt(src):
                fo.write(chunk)
        os.replace(tmp, dst)
