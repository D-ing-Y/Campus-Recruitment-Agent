"""Immutable local blob storage with atomic writes."""

import os
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse


class LocalBlobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def put(self, key: str, data: bytes) -> str:
        path = self._key_path(key)
        if path.exists():
            if path.read_bytes() != data:
                raise FileExistsError(f"immutable blob already exists: {key}")
            return path.as_uri()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return path.as_uri()

    def get(self, uri: str) -> bytes:
        return self._uri_path(uri).read_bytes()

    def exists(self, uri: str) -> bool:
        return self._uri_path(uri).exists()

    def delete(self, uri: str) -> None:
        try:
            self._uri_path(uri).unlink()
        except FileNotFoundError:
            pass

    def _key_path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("blob key escapes storage root")
        if candidate == self.root:
            raise ValueError("blob key must identify a file")
        return candidate

    def _uri_path(self, uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError("LocalBlobStore only accepts file:// URIs")
        path = Path(unquote(parsed.path)).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("blob URI escapes storage root")
        return path
