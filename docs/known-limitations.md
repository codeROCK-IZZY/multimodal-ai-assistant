# Known limitations

Measured where possible. The evaluation report lists every failing case:
[eval/reports/report.md](../eval/reports/report.md).

## Answering
- **Local answers are extractive.** The local provider quotes a sentence (or two adjacent ones). It
  can't combine facts from two places ("how much more is the hotel limit than the meal limit?") or
  reason beyond the text; that needs the Claude provider.
- **Some answerable questions are declined.** "How many eggs do I need?" scores just below the threshold
  against "Whisk four eggs with 300 grams of sugar…". A lower threshold would answer it, and would also
  start answering unanswerable questions; the sweep in the report shows the trade-off.
- **Topic-only matches can slip through.** "Can I freeze the lemon bars?" is answered with the recipe's
  title, because the cross-encoder rates the title as relevant. This is one of the 7 unanswerable test
  questions.
- **Lists that follow a heading.** "What should I bring to the offsite?" misses "Please bring your laptop
  and charger": the notice's OCR merged its heading into "TEAMOFFSITE", and the line itself doesn't
  mention the offsite.
- **Cross-file confusion.** With several files in one conversation, "Which card was used?" is answered
  from a warranty card ("WARRANTY CARD") rather than the receipt ("Card ending 4821").
- **The threshold was calibrated on the evaluation set itself** (32 questions). It is a measured choice,
  not a validated one; a held-out set from real usage is the next step.
- **Search is per conversation.** Files from other conversations aren't searched, and embeddings are
  compared in NumPy. Searching all of a user's files would need pgvector and an ANN index.

## Media
- **OCR degrades on very small or very blurry text:** 14% character error rate at 11 px and 12.5% under
  heavy blur, against 0% in normal conditions. Handwriting is not supported locally.
- **Photos without text:** locally, the assistant reports size, colours and any text found. Recognising
  objects and scenes needs the Claude provider.
- **Speech:** English only with the default model. The benchmark uses synthetic speech, so real
  recordings with background noise, accents, overlapping speakers or phone-line audio will be worse.
  There is no speaker diarisation.
- **Voice messages are limited to 120 s** and are transcribed during the request (a few seconds on CPU).
  Longer recordings should be uploaded as attachments, which are processed in the background (up to
  `MMA_MAX_AUDIO_SECONDS`).
- **No streaming.** Replies arrive complete and speech starts when synthesis finishes; there are no
  partial transcripts and no barge-in.
- **Formats:** no spreadsheets (`.xlsx`), presentations, HEIC photos or video. PDF tables are read as
  text lines, without structure.

## Operations
- **One model copy per process.** Each API process loads about 1.5 GB of models, so the API runs a
  single uvicorn worker and scales out with more tasks rather than more processes.
- **Rate limits are per process** (in-memory). With several API tasks each enforces its own limit; a
  shared store or a WAF rule is the production answer.
- **Files live on a filesystem** (a Docker volume locally, EFS on AWS). Object storage with presigned URLs
  would scale better for large libraries.
- **No antivirus scanning of uploads.** Types are verified from file content and files are never
  executed or served inline as HTML, but a malware scanner should be added before accepting files from
  untrusted users.
