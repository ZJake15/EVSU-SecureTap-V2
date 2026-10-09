"""The .securetap-backup file: everything this copy knows, locked with a
password. Made by `manage.py backup_data` (the launcher's "Back up data"),
unlocked by `manage.py open_backup` (setup's "Bring the data from an older
copy or a backup"), which then hands the folder to import_securetap.

Unlocked, it's a zip laid out like a SecureTap copy's own folder, so the
ordinary import reads it the same way it reads an old copy:

  securetap-backup.json      what's in it and when it was made (MANIFEST)
  backend/db.sqlite3         people and their faces, entry records,
                             accounts, settings, the audit log
  backend/media/...          the photos
  backend/users/occlusion_classifier.joblib    the covered-face model, if any
  backend/.env               TIME_ZONE only
  entry-agent/.env           the gate's own settings only (gate name,
                             camera, card reader) - never the gate key
  launcher_settings.json     the launcher's remembered gate and direction

The lock: the password is turned into a key with scrypt - slow and
memory-hungry on purpose, so a lost or stolen file can't be opened by
trying passwords quickly - and the zip is sealed with AES-256-GCM, which
also notices any change to the file. The zip is encrypted as it's written:
nothing readable is ever put on the backup drive. Without the password
nobody can open it, the project team included.

On disk: HEADER (below), the encrypted zip, then GCM's 16-byte tag. The
header is part of what the tag protects.
"""

import hashlib
import hmac
import io
import json
import secrets
import struct
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"SECURETAP-BACKUP"
VERSION = 1
SUFFIX = ".securetap-backup"
MANIFEST = "securetap-backup.json"
# The same minimum as account passwords.
MIN_PASSWORD_LENGTH = 10

# scrypt's cost: 2**16 x 8 uses 64 MB and about a quarter of a second here.
SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P = 16, 8, 1
_SALT, _CHECK, _NONCE, _TAG = 16, 32, 12, 16
# magic, version, salt, scrypt log2(n) / r / p, password check, nonce
HEADER = struct.Struct(f">{len(MAGIC)}sB{_SALT}sBBB{_CHECK}s{_NONCE}s")
_CHUNK = 1024 * 1024


class BackupError(Exception):
    """Why a backup can't be opened, in words for the setup window."""


def _keys(password, salt, log2_n, r, p):
    """(the AES key, a check value) from one scrypt run. The check lets a
    wrong password be told apart at once, instead of after decrypting the
    whole file; it's a hash of other key material, so it says nothing about
    the AES key itself."""
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** log2_n, r=r, p=p,
                             maxmem=256 * 1024 * 1024, dklen=64)
    return derived[:32], hashlib.sha256(derived[32:]).digest()


class _SealingWriter(io.RawIOBase):
    """A write-only stream that encrypts whatever is written to it on its way
    into `out` - zipfile writes the backup straight through it."""

    def __init__(self, out, encryptor):
        super().__init__()
        self._out = out
        self._encryptor = encryptor

    def writable(self):
        return True

    def write(self, data):
        data = bytes(data)
        self._out.write(self._encryptor.update(data))
        return len(data)


def write_backup(path, password, add_contents):
    """Writes a locked backup to path. add_contents(zip) puts everything in,
    using zip.write / zip.writestr."""
    salt, nonce = secrets.token_bytes(_SALT), secrets.token_bytes(_NONCE)
    key, check = _keys(password, salt, SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P)
    header = HEADER.pack(MAGIC, VERSION, salt, SCRYPT_LOG2_N, SCRYPT_R, SCRYPT_P, check, nonce)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with open(path, "wb") as out:
        out.write(header)
        with zipfile.ZipFile(_SealingWriter(out, encryptor), "w", compression=zipfile.ZIP_DEFLATED) as archive:
            add_contents(archive)
        out.write(encryptor.finalize())
        out.write(encryptor.tag)


def read_backup(path, password, target):
    """Unlocks the backup at path into the (empty) folder target and returns
    its manifest. Raises BackupError for a wrong password or a damaged or
    unknown file."""
    path, target = Path(path), Path(target)
    size = path.stat().st_size
    with open(path, "rb") as source:
        header = source.read(HEADER.size)
        if len(header) < HEADER.size or not header.startswith(MAGIC):
            raise BackupError("That file isn't a SecureTap backup.")
        _magic, version, salt, log2_n, r, p, check, nonce = HEADER.unpack(header)
        if version != VERSION:
            raise BackupError("That backup was made by a newer SecureTap - update this copy first.")
        # Bounds, so a damaged header can't ask for gigabytes of memory.
        if not (10 <= log2_n <= 20 and 1 <= r <= 16 and 1 <= p <= 4) or size < HEADER.size + _TAG:
            raise BackupError("That backup file is damaged.")
        key, expected = _keys(password, salt, log2_n, r, p)
        if not hmac.compare_digest(check, expected):
            raise BackupError("Wrong password for this backup.")

        source.seek(size - _TAG)
        tag = source.read(_TAG)
        source.seek(HEADER.size)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        unlocked = target / "unlocked-backup.zip"
        try:
            with open(unlocked, "wb") as out:
                remaining = size - HEADER.size - _TAG
                while remaining:
                    chunk = source.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise BackupError("That backup file is incomplete.")
                    remaining -= len(chunk)
                    out.write(decryptor.update(chunk))
                try:
                    out.write(decryptor.finalize())
                except InvalidTag:
                    raise BackupError("That backup file is damaged or was changed, so it can't be used.") from None
            with zipfile.ZipFile(unlocked) as archive:
                root = target.resolve()
                if any(not (root / name).resolve().is_relative_to(root) for name in archive.namelist()):
                    raise BackupError("That backup file is damaged.")
                if MANIFEST not in archive.namelist():
                    raise BackupError("That backup file is incomplete.")
                archive.extractall(target)
                return json.loads(archive.read(MANIFEST))
        finally:
            unlocked.unlink(missing_ok=True)


def is_unlocked_backup(folder):
    """Whether folder is an unlocked backup (read_backup's target)."""
    return (Path(folder) / MANIFEST).is_file()
