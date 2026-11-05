"""Optional encryption for backup archives (CROWNOPS_BACKUP_KEY).

Format (version 1), streamed so large upload folders never sit in memory:

    header  = MAGIC (16) | version (1) | key id (8) | nonce prefix (7)          -> 32 bytes
    chunk_i = length (4, big endian) | AES-256-GCM(chunk plaintext, tag 16)
    nonce_i = nonce prefix (7) | i (4, big endian) | last flag (1)

The whole header is the associated data of every chunk, so the header cannot be swapped.
The counter in the nonce detects reordered or duplicated chunks; the last flag detects an
archive truncated on a chunk boundary. The key id lets an operator tell which key a file
needs without revealing anything about the key.

The backup key is deliberately separate from CROWNOPS_SECRET_KEY: an attacker holding the
encrypted archive and the application key still cannot read it, and losing the application
key does not make backups unreadable (MFA users can be reset with `reset-mfa`).
"""
import base64
import hashlib
import hmac
import os
import secrets
import tempfile
from contextlib import contextmanager
from pathlib import Path

MAGIC = b'CROWNOPS-BACKUP\x00'
VERSION = 1
HEADER_BYTES = 32
CHUNK = 1024 * 1024
ENCRYPTED_SUFFIX = '.enc'


class BackupKeyError(ValueError):
    pass


def generate_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def parse_key(value):
    try:
        raw = base64.urlsafe_b64decode(value.encode() + b'=' * (-len(value) % 4))
    except (ValueError, TypeError):
        raise BackupKeyError('CROWNOPS_BACKUP_KEY is not valid. Generate one with `manage.py backup-key`.') from None
    if len(raw) != 32:
        raise BackupKeyError('CROWNOPS_BACKUP_KEY must encode exactly 32 bytes. Generate one with `manage.py backup-key`.')
    return raw


def configured_key():
    """The backup key from the environment, or None when backups are not encrypted."""
    value = os.getenv('CROWNOPS_BACKUP_KEY', '').strip()
    if not value:
        return None
    if value == os.getenv('CROWNOPS_SECRET_KEY', '').strip():
        raise BackupKeyError('CROWNOPS_BACKUP_KEY must differ from CROWNOPS_SECRET_KEY.')
    return parse_key(value)


def key_id(raw):
    return hmac.new(raw, b'crownops-backup-key-id', hashlib.sha256).digest()[:8]


def is_encrypted(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _nonce(prefix, index, last):
    return prefix + index.to_bytes(4, 'big') + (b'\x01' if last else b'\x00')


def encrypt_file(source, dest, raw):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes = AESGCM(raw)
    header = MAGIC + bytes([VERSION]) + key_id(raw) + secrets.token_bytes(7)
    prefix = header[25:32]
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out, open(source, 'rb') as src:
            out.write(header)
            index, chunk = 0, src.read(CHUNK)
            while True:
                following = src.read(CHUNK)
                last = not following
                sealed = aes.encrypt(_nonce(prefix, index, last), chunk, header)
                out.write(len(sealed).to_bytes(4, 'big') + sealed)
                if last:
                    break
                index += 1
                if index >= 2 ** 32:
                    raise ValueError('Backup is too large to encrypt.')
                chunk = following
    except Exception:
        Path(dest).unlink(missing_ok=True)
        raise


def decrypt_file(source, dest, raw):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes = AESGCM(raw)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out, open(source, 'rb') as src:
            header = src.read(HEADER_BYTES)
            if len(header) != HEADER_BYTES or header[:16] != MAGIC:
                raise ValueError('This is not an encrypted CrownOps backup.')
            if header[16] != VERSION:
                raise ValueError('Unsupported encrypted backup version.')
            if not hmac.compare_digest(header[17:25], key_id(raw)):
                raise BackupKeyError('This backup was encrypted with a different backup key (key id '
                                     + header[17:25].hex() + '; configured key id ' + key_id(raw).hex() + ').')
            prefix, index = header[25:32], 0
            size = src.read(4)
            while True:
                if len(size) != 4:
                    raise ValueError('Encrypted backup is truncated.')
                length = int.from_bytes(size, 'big')
                if not 16 <= length <= CHUNK + 16:
                    raise ValueError('Encrypted backup is corrupt.')
                sealed = src.read(length)
                if len(sealed) != length:
                    raise ValueError('Encrypted backup is truncated.')
                size = src.read(4)
                last = not size
                try:
                    out.write(aes.decrypt(_nonce(prefix, index, last), sealed, header))
                except InvalidTag:
                    raise ValueError('Encrypted backup failed authentication (corrupt, truncated or altered).') from None
                if last:
                    break
                index += 1
    except Exception:
        Path(dest).unlink(missing_ok=True)
        raise


def key_for():
    """The configured key, or a clear error when an encrypted archive needs one."""
    raw = configured_key()
    if raw is None:
        raise BackupKeyError('This backup is encrypted. Set CROWNOPS_BACKUP_KEY to the key used when it was made.')
    return raw


@contextmanager
def plain_archive(archive):
    """Yield a readable ZIP path: the archive itself, or a private decrypted copy that is
    deleted afterwards."""
    if not is_encrypted(archive):
        yield Path(archive)
        return
    raw = key_for()
    with tempfile.TemporaryDirectory(prefix='crownops-decrypt-') as tmp:
        target = Path(tmp) / 'archive.zip'
        decrypt_file(archive, target, raw)
        yield target
