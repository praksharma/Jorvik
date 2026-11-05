"""Bounded-memory SHA-256 checks for sweep files (independent of Qt)."""
import hashlib
import re


def file_integrity(path):
    """Hash exact file bytes in chunks; never load a full shard into memory."""
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {'sha256': digest.hexdigest(), 'size_bytes': size}


def validate_integrity(value):
    if (not isinstance(value, dict)
            or not isinstance(value.get('sha256'), str)
            or re.fullmatch(r'[0-9a-f]{64}', value['sha256']) is None
            or type(value.get('size_bytes')) is not int or value['size_bytes'] < 0):
        raise ValueError('Invalid sweep SHA-256 checksum or byte count.')


def verify_integrity(path, expected):
    validate_integrity(expected)
    if file_integrity(path) != expected:
        raise ValueError(f'Sweep checksum mismatch: {path.name}')
