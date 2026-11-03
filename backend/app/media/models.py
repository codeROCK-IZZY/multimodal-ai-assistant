"""Local models, loaded lazily and shared per process.

| Capability | Model | Runtime |
|---|---|---|
| Speech → text | Whisper (``MMA_STT_MODEL``, default ``base.en``) via faster-whisper | CTranslate2, int8, CPU |
| Text → speech | Piper voice (``MMA_TTS_VOICE``) | ONNX Runtime, CPU |
| Image → text | RapidOCR (PP-OCR detection + recognition) | ONNX Runtime, CPU |
| Text → vector | ``BAAI/bge-small-en-v1.5`` via fastembed | ONNX Runtime, CPU |
| Relevance (question, passage) | ``Xenova/ms-marco-MiniLM-L-6-v2`` cross-encoder via fastembed | ONNX Runtime, CPU |

All run offline once downloaded (``MMA_MODEL_CACHE_DIR``; the Docker image bakes them in).
"""

from __future__ import annotations

import io
import threading
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_lock = threading.Lock()


def _cache() -> str | None:
    return get_settings().model_cache_dir


@lru_cache
def whisper() -> Any:
    from faster_whisper import WhisperModel

    s = get_settings()
    with _lock:
        return WhisperModel(
            s.stt_model,
            device="cpu",
            compute_type=s.stt_compute_type,
            download_root=_cache(),
            cpu_threads=s.model_threads or 0,
        )


def piper_voice_files(voice: str) -> tuple[str, str]:
    """Download (once) the Piper voice model and its config from the official voices repository."""
    from huggingface_hub import hf_hub_download

    lang, name, quality = voice.split("-")  # e.g. en_US-ljspeech-medium
    family = lang.split("_")[0]
    base = f"{family}/{lang}/{name}/{quality}/{voice}"
    onnx = hf_hub_download("rhasspy/piper-voices", f"{base}.onnx", cache_dir=_cache())
    cfg = hf_hub_download("rhasspy/piper-voices", f"{base}.onnx.json", cache_dir=_cache())
    return onnx, cfg


@lru_cache
def piper() -> Any:
    from piper import PiperVoice

    onnx, cfg = piper_voice_files(get_settings().tts_voice)
    with _lock:
        return PiperVoice.load(onnx, config_path=cfg)


def synthesize(text: str, length_scale: float | None = None) -> bytes:
    """Text → WAV bytes (16-bit mono)."""
    from piper import SynthesisConfig

    voice = piper()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=length_scale))
    return buf.getvalue()


@lru_cache
def ocr_engine() -> Any:
    import logging

    from rapidocr import RapidOCR

    with _lock:
        engine = RapidOCR()
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)  # it logs every model load at INFO
    return engine


@lru_cache
def embedder() -> Any:
    from fastembed import TextEmbedding

    s = get_settings()
    with _lock:
        return TextEmbedding(s.embedding_model, cache_dir=_cache(), threads=s.model_threads)


@lru_cache
def reranker() -> Any:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    s = get_settings()
    with _lock:
        return TextCrossEncoder(s.reranker_model, cache_dir=_cache(), threads=s.model_threads)


def rerank(query: str, documents: list[str]) -> list[float]:
    """Cross-encoder relevance of each document to the query (logits; higher = more relevant)."""
    return [float(x) for x in reranker().rerank(query, documents, batch_size=32)]


def embed_passages(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in embedder().passage_embed(texts, batch_size=32)]


def embed_query(text: str) -> list[float]:
    return [float(x) for x in next(iter(embedder().query_embed(text)))]


def warm_up(path: Path | None = None) -> None:
    """Download every model (used when building the Docker image)."""
    whisper()
    piper()
    ocr_engine()
    embed_query("warm up")
    rerank("warm up", ["warm up"])
