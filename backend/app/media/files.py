"""Uploaded files: storage and type detection.

Type detection uses the file's **content** (magic bytes), not its name or the browser's claimed
MIME type, so a script renamed to ``photo.jpg`` is rejected. Files are stored under random keys;
user-supplied names are never used as paths.
"""

from __future__ import annotations

import hashlib
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import UnprocessableData


@dataclass(frozen=True)
class Detected:
    kind: str  # image | document | audio
    mime: str


def detect(data: bytes, filename: str) -> Detected:
    head = data[:16]
    name = filename.lower()
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Detected("image", "image/png")
    if head.startswith(b"\xff\xd8\xff"):
        return Detected("image", "image/jpeg")
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return Detected("image", "image/webp")
    if head.startswith((b"GIF87a", b"GIF89a")):
        return Detected("image", "image/gif")
    if head.startswith(b"%PDF-"):
        return Detected("document", "application/pdf")
    if head.startswith(b"PK\x03\x04") and name.endswith(".docx"):
        try:
            if "word/document.xml" in zipfile.ZipFile(BytesIO(data)).namelist():
                return Detected("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        except zipfile.BadZipFile:
            pass
    if head[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return Detected("audio", "audio/wav")
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return Detected("audio", "audio/mpeg")
    if head.startswith(b"OggS"):
        return Detected("audio", "audio/ogg")
    if head.startswith(b"fLaC"):
        return Detected("audio", "audio/flac")
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return Detected("audio", "audio/webm")  # browser voice recordings (MediaRecorder)
    if data[4:8] == b"ftyp" and name.endswith((".m4a", ".mp4", ".aac")):
        return Detected("audio", "audio/mp4")
    if name.endswith((".txt", ".md", ".markdown", ".csv")):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnprocessableData("Text files must be UTF-8") from exc
        return Detected("document", "text/plain")
    raise UnprocessableData(
        "Unsupported file type. Use an image (PNG, JPEG, WebP, GIF), a document "
        "(PDF, DOCX, TXT) or audio (WAV, MP3, M4A, OGG, WebM, FLAC)."
    )


class FileStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("invalid key")
        return p

    def put(self, data: bytes, suffix: str = "") -> tuple[str, str]:
        """Store bytes under a new random key. Returns (key, sha256)."""
        key = f"{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}{suffix}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key, hashlib.sha256(data).hexdigest()

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def store() -> FileStore:
    return FileStore(get_settings().file_dir)
