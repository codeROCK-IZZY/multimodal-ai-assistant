# AI card — the models, what they do, and what they never do

## Models

| Task | Model | Runtime | Licence | Where it runs |
|---|---|---|---|---|
| Speech → text | Whisper `base.en` (`Systran/faster-whisper-base.en`) | CTranslate2, int8, CPU | MIT | API (voice messages), worker (uploads) |
| Text → speech | Piper `en_US-ljspeech-medium` | ONNX Runtime | Piper: MIT; voice trained on LJ Speech (public domain) | API |
| Image → text | RapidOCR (PP-OCR detection, classification, recognition) | ONNX Runtime | Apache-2.0 | Worker |
| Text → vector | `BAAI/bge-small-en-v1.5` via fastembed | ONNX Runtime | MIT | API, worker |
| Answerability / reranking | `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder via fastembed | ONNX Runtime | Apache-2.0 | API |
| Open-ended answers, photo understanding (optional) | Claude `claude-opus-5-5` | Anthropic API | Commercial API terms | Anthropic |

All local models are downloaded once (`python -m app.cli warm-models`) and baked into the Docker image.
The container then runs with `HF_HUB_OFFLINE=1`.

## Intended use

- Answering questions about the user's own files, with citations, and saying so when the files don't
  contain the answer.
- Transcribing the user's voice messages and recordings; reading replies aloud.
- Small actions on the user's own data (tasks, memory), under permissions the user sets.

## Not intended for

- **Decisions with legal, medical or financial consequences** based on extracted text. OCR and speech
  recognition make errors (see the evaluation); always check the cited source.
- Identifying people in photos or recordings. The local provider does no face or speaker recognition,
  and the Claude provider is not asked to.
- Transcribing people who haven't agreed to be recorded.
- Languages other than English. The default Whisper model is English-only and the voice is US English.

## What the models never do

- **Never act on instructions found in files.** The local provider derives intents only from the user's
  words. With Claude, any data-changing tool requested while files are in context needs the user's
  confirmation, enforced in code (see [DESIGN.md](DESIGN.md#the-permission-gate)).
- **Never save a memory the user didn't ask for.** The local provider saves only on "remember …". Claude
  is instructed the same way, and the memory switch disables saving and use entirely.
- **Local mode never generates facts.** Answers are quotes from the user's files or memories.

## Data flows

| Mode | What leaves the server |
|---|---|
| Local (default) | Nothing |
| Claude | For each message: the conversation's recent messages, the file text (or the most relevant 12 passages), the user's memories, up to 3 images from the conversation, and tool results. Sent to the Anthropic API under its data-retention terms |

The UI shows the active mode in the header and on every reply.

## Evaluation summary

Measured by `python -m app.cli evaluate` on synthetic data; the full report is in
[eval/reports/report.md](../eval/reports/report.md).

- OCR: 0% character error rate on clean, small, blurred, rotated, noisy and low-contrast renders. On
  harder conditions (11 px text, heavy blur) the error rate rises to 12–14%.
- Speech: about 4% word error rate across three speaking rates (2% at normal speed, 6% fast). Errors are mostly
  near-homophones and number formatting.
- Q&A: 92% answer accuracy, at least 95% citation accuracy, and 86% correct refusals on unanswerable
  questions.
- Prompt injection: 0 data changes across 6 attack cases, including a model that obeys the injection.

**Caveats.**
- The speech benchmark uses synthetic (Piper) speech, which is cleaner than real recordings. Expect a
  higher error rate on real audio with noise, accents or several speakers.
- The Q&A set is small (32 questions over 7 files), and the answerability threshold was calibrated on
  that same set. Treat the Q&A figures as indicative, not as a guarantee.
