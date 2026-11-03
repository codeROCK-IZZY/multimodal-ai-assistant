"""Turn an attachment into text the assistant can search and cite.

* **Image** → OCR with RapidOCR; text boxes are ordered into reading order (top to bottom, then left
  to right) and grouped into lines. Image properties (size, dominant colours, brightness) are kept
  as metadata. Scene understanding beyond text needs the Claude provider.
* **Document** → PDF text per page with pdfplumber (pages with almost no text are rendered and
  OCR'd, so scans work), DOCX paragraphs and tables, plain text.
* **Audio** → transcription with Whisper (faster-whisper, voice-activity filtering), with timestamps.

Each result is split into **chunks with a locator** ("page 2", "lines 4-9", "00:41-01:05") so answers
can cite exactly where a fact came from.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from app.core.config import get_settings
from app.core.errors import UnprocessableData
from app.media import models

MAX_IMAGE_SIDE = 2000
CHUNK_CHARS = 600


@dataclass
class Extracted:
    text: str
    chunks: list[tuple[str, str]]  # (locator, text)
    meta: dict[str, Any] = field(default_factory=dict)
    processor: str = ""


# ---------------------------------------------------------------------- images
COLOURS = {
    "black": (20, 20, 20),
    "white": (240, 240, 240),
    "grey": (128, 128, 128),
    "red": (200, 40, 40),
    "orange": (230, 140, 30),
    "yellow": (230, 210, 50),
    "green": (50, 160, 60),
    "blue": (40, 90, 200),
    "purple": (130, 60, 170),
    "brown": (120, 80, 40),
    "pink": (230, 130, 170),
}


def _colour_name(rgb: tuple[int, int, int]) -> str:
    return min(COLOURS, key=lambda n: sum((a - b) ** 2 for a, b in zip(COLOURS[n], rgb, strict=True)))


def load_image(data: bytes) -> Image.Image:
    try:
        img: Image.Image = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as exc:  # truncated or not really an image
        raise UnprocessableData("The image could not be read") from exc
    if max(img.size) > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    return img


def ocr_lines(img: Image.Image) -> tuple[list[str], float | None]:
    """OCR an image into reading-order lines. Returns (lines, mean confidence)."""
    result = models.ocr_engine()(np.asarray(img))
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or txts is None or len(txts) == 0:
        return [], None
    items = []
    for box, txt, score in zip(boxes, txts, scores or [1.0] * len(txts), strict=False):
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append({"top": min(ys), "bottom": max(ys), "left": min(xs), "text": str(txt).strip(), "score": score})
    items.sort(key=lambda b: (b["top"], b["left"]))
    lines: list[list[dict[str, Any]]] = []
    for it in items:
        if lines:
            last = lines[-1]
            mid = (it["top"] + it["bottom"]) / 2
            if last[0]["top"] <= mid <= last[0]["bottom"]:  # same visual line
                last.append(it)
                continue
        lines.append([it])
    text_lines = [" ".join(b["text"] for b in sorted(line, key=lambda b: b["left"])) for line in lines]
    mean = float(np.mean([float(s) for s in (scores or [])])) if scores is not None and len(scores) else None
    return [t for t in text_lines if t], mean


def process_image(data: bytes) -> Extracted:
    img = load_image(data)
    lines, confidence = ocr_lines(img)
    small = img.resize((64, 64))
    palette = small.quantize(colors=5).convert("RGB").getcolors(64 * 64) or []
    palette.sort(reverse=True)
    # colours covering at least 8% of the image (ignores anti-aliasing fringes around text)
    big = [c for n, c in palette if n >= 0.08 * 64 * 64]
    colours = list(dict.fromkeys(_colour_name(c) for c in big))[:3]  # type: ignore[arg-type]
    brightness = float(np.asarray(small.convert("L")).mean() / 255)
    chunks = [(f"lines {i + 1}-{min(i + 6, len(lines))}", "\n".join(lines[i : i + 6])) for i in range(0, len(lines), 6)]
    return Extracted(
        text="\n".join(lines),
        chunks=chunks,
        meta={
            "width": img.width,
            "height": img.height,
            "ocr_lines": len(lines),
            "ocr_confidence": confidence,
            "dominant_colours": colours,
            "brightness": round(brightness, 2),
        },
        processor="rapidocr",
    )


# ---------------------------------------------------------------------- documents
def _windows(locator: str, text: str) -> list[tuple[str, str]]:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z0-9•\-])", text) if p.strip()]
    out: list[tuple[str, str]] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) > CHUNK_CHARS:
            out.append((locator, buf))
            buf = ""
        buf = f"{buf}\n{p}".strip()
    if buf:
        out.append((locator, buf))
    return out


def process_document(data: bytes, mime: str) -> Extracted:
    s = get_settings()
    if mime == "application/pdf":
        import pdfplumber

        pages: list[str] = []
        ocr_pages = 0
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > s.max_pdf_pages:
                raise UnprocessableData(f"PDFs are limited to {s.max_pdf_pages} pages")
            for page in pdf.pages:
                text = page.extract_text() or ""
                if len(text.strip()) < 20:  # scanned page: render and OCR it
                    img = page.to_image(resolution=200).original.convert("RGB")
                    lines, _ = ocr_lines(img)
                    text = "\n".join(lines)
                    ocr_pages += 1
                pages.append(text)
        chunks = [c for i, t in enumerate(pages) for c in _windows(f"page {i + 1}", t)]
        return Extracted(
            "\n\n".join(pages),
            chunks,
            {"pages": len(pages), "ocr_pages": ocr_pages},
            "pdfplumber" + ("+rapidocr" if ocr_pages else ""),
        )
    if mime.endswith("wordprocessingml.document"):
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for table in d.tables:
            rows = [[c.text.strip() for c in row.cells] for row in table.rows]
            header = rows[0] if len(rows) > 1 and all(rows[0]) else None
            for row in rows[1:] if header else rows:
                # "Owner: Priya Raman; Due: 2026-12-04" keeps each value next to its column name
                parts.append(
                    "; ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False)) if header else " | ".join(row)
                )
        text = "\n".join(parts)
        return Extracted(text, _windows("document", text), {"paragraphs": len(parts)}, "python-docx")
    text = data.decode("utf-8")
    return Extracted(text, _windows("text", text), {"characters": len(text)}, "plain text")


# ---------------------------------------------------------------------- audio
def _ts(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def process_audio(data: bytes, max_seconds: int | None = None) -> Extracted:
    s = get_settings()
    limit = max_seconds or s.max_audio_seconds
    model = models.whisper()
    try:
        segments_iter, info = model.transcribe(
            io.BytesIO(data), beam_size=1, vad_filter=True, language="en" if s.stt_model.endswith(".en") else None
        )
    except Exception as exc:  # undecodable container / codec
        raise UnprocessableData("The recording could not be decoded") from exc
    if info.duration > limit:
        raise UnprocessableData(f"Recordings are limited to {limit // 60} minutes")
    segments = [
        {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()} for seg in segments_iter
    ]
    chunks: list[tuple[str, str]] = []
    window: list[dict[str, Any]] = []
    for seg in segments:
        window.append(seg)
        if seg["end"] - window[0]["start"] >= 25:
            chunks.append((f"{_ts(window[0]['start'])}-{_ts(seg['end'])}", " ".join(w["text"] for w in window)))
            window = []
    if window:
        chunks.append((f"{_ts(window[0]['start'])}-{_ts(window[-1]['end'])}", " ".join(w["text"] for w in window)))
    text = " ".join(seg["text"] for seg in segments)
    return Extracted(
        text=text,
        chunks=chunks,
        meta={
            "duration_seconds": round(info.duration, 1),
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "segments": segments,
        },
        processor=f"whisper-{s.stt_model}",
    )


def process(kind: str, data: bytes, mime: str) -> Extracted:
    if kind == "image":
        return process_image(data)
    if kind == "document":
        return process_document(data, mime)
    return process_audio(data)
