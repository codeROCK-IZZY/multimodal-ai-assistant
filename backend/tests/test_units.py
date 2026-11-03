"""Pure unit tests: no database, no models."""

import io
from datetime import date

import pytest

from app.assistant import intents
from app.assistant.retrieval import lexical, split_units, terms
from app.assistant.tools import ToolError, convert, safe_eval
from app.core.errors import UnprocessableData
from app.evaluation.run import cer, normalise_words, wer
from app.media.files import FileStore, detect
from app.modules.chat.router import speech_text
from app.synthetic import samples

MONDAY = date(2026, 11, 2)


# ---------------------------------------------------------------- calculator
@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ("2 + 3 * 4", 14),
        ("(450 + 120) / 3", 190),
        ("2 ^ 10", 1024),
        ("1,200 * 3", 3600),
        ("7 × 6", 42),
        ("-5 + 2", -3),
        ("10 % 4", 2),
        ("9 // 2", 4),
    ],
)
def test_safe_eval(expr, value):
    assert safe_eval(expr) == value


@pytest.mark.parametrize(
    "expr", ["__import__('os')", "a + 1", "len([1])", "(1).real", "9 ** 999999", "1 / 0", "", "2 +"]
)
def test_safe_eval_rejects_anything_but_arithmetic(expr):
    with pytest.raises(ToolError):
        safe_eval(expr)


# ---------------------------------------------------------------- units
def test_convert_units():
    assert convert(5, "km", "miles") == pytest.approx(3.10686, rel=1e-4)
    assert convert(72, "°F", "c") == pytest.approx(22.2222, rel=1e-4)
    assert convert(0, "c", "k") == pytest.approx(273.15)
    assert convert(2, "liters", "cups") == pytest.approx(8.4535, rel=1e-3)
    with pytest.raises(ToolError, match="cannot convert"):
        convert(1, "kg", "km")
    with pytest.raises(ToolError, match="unknown unit"):
        convert(1, "parsec", "km")


# ---------------------------------------------------------------- intents
@pytest.mark.parametrize(
    ("text", "kind", "tool"),
    [
        ("hello", "help", None),
        ("What is 12 * 7?", "tool", "calculator"),
        ("what's 15% of 240", "tool", "calculator"),
        ("Convert 5 km to miles", "tool", "convert_units"),
        ("how many cups in 2 liters", "tool", "convert_units"),
        ("what's the date today", "tool", "current_datetime"),
        ("Remind me to call mum tomorrow", "tool", "create_task"),
        ("what are my tasks", "tool", "list_tasks"),
        ("Remember that my locker code is 4471", "tool", "save_memory"),
        ("forget everything", "tool", "forget_memory"),
        ("What do you remember about me?", "list_memories", None),
        ("Summarize the policy", "summarize", None),
        ("transcribe the recording", "read", None),
        ("what does the receipt say", "read", None),
        ("describe this image", "describe", None),
        ("What is the hotel limit per night?", "question", None),
        ("When is the meeting on 2026-11-30?", "question", None),
    ],
)
def test_intents(text, kind, tool):
    it = intents.recognise(text, MONDAY)
    assert (it.kind, it.tool) == (kind, tool)


def test_task_due_dates():
    assert intents.recognise("Remind me to pay rent tomorrow", MONDAY).args == {
        "title": "Pay rent",
        "due": "2026-11-03",
    }
    assert intents.recognise("remind me to send slides on friday", MONDAY).args["due"] == "2026-11-06"
    assert intents.recognise("remind me to call Sam on monday", MONDAY).args["due"] == "2026-11-09"  # next Monday
    assert intents.recognise("add a task to renew passport by 2026-12-01", MONDAY).args == {
        "title": "Renew passport",
        "due": "2026-12-01",
    }
    assert intents.recognise("remind me to stretch", MONDAY).args["due"] is None


def test_memory_intent_normalises_the_fact():
    it = intents.recognise("remember that my sister's birthday is 3 May", MONDAY)
    assert it.args == {"fact": "My sister's birthday is 3 May"}
    assert intents.recognise("Forget my locker code", MONDAY).args == {"about": "locker code"}


# ---------------------------------------------------------------- files
def test_detect_by_content_not_name():
    png = samples.text_image(["hi"])
    assert detect(png, "anything.bin").kind == "image"
    assert detect(samples.text_pdf([["x"]]), "a.pdf").mime == "application/pdf"
    assert detect(samples.docx_file(["T", "p"], [["a", "b"], ["1", "2"]]), "notes.docx").kind == "document"
    assert detect(b"RIFF\x00\x00\x00\x00WAVEfmt ", "a.wav").kind == "audio"
    assert detect("héllo".encode(), "notes.txt").mime == "text/plain"
    with pytest.raises(UnprocessableData):
        detect(b"#!/bin/sh\nrm -rf /", "photo.jpg")  # a script renamed to .jpg
    with pytest.raises(UnprocessableData, match="UTF-8"):
        detect(b"\xff\xfe\x00bad", "notes.txt")
    with pytest.raises(UnprocessableData):
        detect(b"PK\x03\x04not-a-zip", "fake.docx")


def test_file_store_uses_random_keys_and_blocks_traversal(tmp_path):
    store = FileStore(tmp_path)
    key, sha = store.put(b"data", ".wav")
    assert key.endswith(".wav") and len(sha) == 64 and store.get(key) == b"data"
    with pytest.raises(ValueError):
        store.get("../../etc/passwd")
    store.delete(key)
    store.delete(key)  # idempotent


def test_generated_pdf_has_extractable_text_per_page():
    import pdfplumber

    pdf = samples.text_pdf([["Page one (first)"], ["Second page \\ backslash"]])
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        assert [p.extract_text() for p in doc.pages] == ["Page one (first)", "Second page \\ backslash"]


# ---------------------------------------------------------------- retrieval helpers / speech text
def test_terms_and_lexical_keep_identifiers():
    t = terms("What is the serial number KX4471-09 on the card?")
    assert "kx4471-09" in t and "the" not in t
    assert lexical({"kx4471-09"}, "Serial number: KX4471-09") == 1.0


def test_split_units_sentences_and_lines():
    assert split_units("TOTAL 23.85\nCard ending 4821") == ["TOTAL 23.85", "Card ending 4821"]
    assert split_units("First sentence. Second one! 3rd?") == ["First sentence.", "Second one!", "3rd?"]


def test_speech_text_strips_markdown_and_citations():
    assert (
        speech_text('"TOTAL 23.85" — cafe-receipt.png [1]\n- **bold** item')
        == '"TOTAL 23.85" — cafe-receipt.png\nbold item'
    )


# ---------------------------------------------------------------- metrics
def test_error_rates():
    assert cer("Room 4B", "Room 4B") == 0 and cer("TEAM OFFSITE", "TEAMOFFSITE") == 0
    assert cer("abcd", "abed") == 0.25
    assert normalise_words("Gate 12 at 7:45.") == ["gate", "twelve", "at", "seven", "forty", "five"]
    assert wer("leaves at seven forty five", "Leaves at 7.45.") == 0
    assert wer("a b c d", "a x c") == 0.5
