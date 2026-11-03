import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Attachment, AttachmentDetail, Citation, ToolEvent, ToolRequest } from "../api/types";
import { bytes } from "../lib/format";
import { Badge, Button, Spinner } from "./ui";

// ------------------------------------------------------------------ rich text (no HTML injection)
function inline(text: string, onCite?: (n: number) => void): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\[\d{1,3}\])/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={i++}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("[")) {
      const n = Number(tok.slice(1, -1));
      out.push(
        <button key={i++} type="button" onClick={() => onCite?.(n)} aria-label={`Show source ${n}`}
          className="mx-0.5 rounded bg-indigo-100 px-1 align-baseline text-[11px] font-semibold text-indigo-700 hover:bg-indigo-200">
          {n}
        </button>,
      );
    } else out.push(<em key={i++}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Renders the small markdown subset replies use: **bold**, *italic*, "- " lists, citations [n]. */
export function RichText({ text, onCite }: { text: string; onCite?: (n: number) => void }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={blocks.length} className="ml-5 list-disc space-y-0.5">{list.map((l, j) => <li key={j}>{inline(l, onCite)}</li>)}</ul>);
      list = [];
    }
  };
  for (const line of text.split("\n")) {
    if (/^\s*- /.test(line)) list.push(line.replace(/^\s*- /, ""));
    else {
      flush();
      if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line, onCite)}</p>);
    }
  }
  flush();
  return <div className="space-y-1.5 leading-relaxed">{blocks}</div>;
}

// ------------------------------------------------------------------ citations and tool events
export function Citations({ items, active, onSelect }: { items: Citation[]; active: number | null; onSelect: (n: number | null) => void }) {
  if (!items.length) return null;
  const shown = items.find((c) => c.n === active);
  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex flex-wrap gap-1.5">
        {items.map((c) => (
          <button key={c.n} type="button" onClick={() => onSelect(active === c.n ? null : c.n)}
            className={clsx("rounded-full border px-2 py-0.5 text-xs", active === c.n
              ? "border-indigo-300 bg-indigo-50 text-indigo-800" : "border-slate-200 bg-white text-slate-600 hover:border-slate-300")}>
            <span className="font-semibold">{c.n}</span> · {c.source === "memory" ? "your memory" : `${c.filename} · ${c.locator}`}
          </button>
        ))}
      </div>
      {shown && shown.quote && (
        <blockquote className="rounded-lg border-l-4 border-indigo-300 bg-slate-50 px-3 py-2 text-xs text-slate-700 whitespace-pre-wrap">
          {shown.quote}
        </blockquote>
      )}
    </div>
  );
}

const TOOL_LABELS: Record<string, string> = {
  calculator: "Calculator", convert_units: "Unit conversion", current_datetime: "Date & time", create_task: "Create task",
  list_tasks: "List tasks", save_memory: "Save memory", forget_memory: "Forget memory",
};
export const toolLabel = (name: string) => TOOL_LABELS[name] ?? name;

export function ToolEvents({ events }: { events: ToolEvent[] }) {
  if (!events.length) return null;
  const tone = (s: string): "green" | "amber" | "slate" | "red" =>
    s === "executed" ? "green" : s === "needs_permission" ? "amber" : s === "denied" ? "slate" : "red";
  const labels: Record<string, string> = { executed: "ran", needs_permission: "awaiting confirmation", denied: "blocked by settings" };
  const label = (s: string) => labels[s] ?? s;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {events.map((e, i) => (
        <span key={i} title={e.reason ?? e.error ?? JSON.stringify(e.args)}>
          <Badge tone={tone(e.status)}>{toolLabel(e.name)} · {label(e.status)}</Badge>
        </span>
      ))}
    </div>
  );
}

function describeArgs(r: ToolRequest): string {
  const a = r.args;
  if (r.tool === "create_task") return `${a.title}${a.due ? ` · due ${a.due}` : ""}`;
  if (r.tool === "forget_memory") return a.about === "everything" ? "everything I remember" : `memories about “${a.about}”`;
  if (r.tool === "save_memory") return String(a.fact);
  return JSON.stringify(a);
}

export function ToolRequestCard({ request, onDecide, busy }: {
  request: ToolRequest; onDecide: (approve: boolean) => void; busy: boolean;
}) {
  return (
    <div role="group" aria-label={`Confirm ${toolLabel(request.tool)}`}
      className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm">
      <div className="min-w-0 flex-1">
        <p className="font-medium text-amber-900">{toolLabel(request.tool)} needs your confirmation</p>
        <p className="truncate text-amber-800">{describeArgs(request)}</p>
      </div>
      <Button variant="secondary" disabled={busy} onClick={() => onDecide(false)}>Decline</Button>
      <Button disabled={busy} onClick={() => onDecide(true)}>Confirm</Button>
    </div>
  );
}

// ------------------------------------------------------------------ attachments
const KIND_ICON: Record<Attachment["kind"], string> = { image: "🖼", document: "📄", audio: "🎙" };

function useBlobUrl(path: string | null) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let revoked = false;
    let made: string | null = null;
    api<Blob>(path, { raw: true }).then((b) => {
      if (revoked) return;
      made = URL.createObjectURL(b);
      setUrl(made);
    }).catch(() => undefined);
    return () => {
      revoked = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [path]);
  return url;
}

/** One attachment: polls while processing, shows a thumbnail for images and the extracted text on demand. */
export function AttachmentChip({ attachment, onReady, onDelete }: {
  attachment: Attachment; onReady?: () => void; onDelete?: () => void;
}) {
  const pending = attachment.status === "queued" || attachment.status === "processing";
  const q = useQuery({
    queryKey: ["attachment", attachment.id],
    queryFn: () => api<AttachmentDetail>(`/attachments/${attachment.id}`),
    refetchInterval: (query) => (["queued", "processing"].includes(query.state.data?.status ?? attachment.status) ? 1500 : false),
  });
  const a = q.data ?? attachment;
  const ready = a.status === "ready";
  const notified = useRef(!pending);
  useEffect(() => {
    if (ready && !notified.current) {
      notified.current = true;
      onReady?.();
    }
  }, [ready, onReady]);
  const thumb = useBlobUrl(a.kind === "image" && ready ? `/attachments/${a.id}/file` : null);
  const [open, setOpen] = useState(false);
  return (
    <div className="w-60 shrink-0 rounded-xl border border-slate-200 bg-white text-xs shadow-sm">
      <div className="flex items-center gap-2 p-2">
        {thumb ? <img src={thumb} alt="" className="h-10 w-10 rounded object-cover" />
          : <span aria-hidden className="grid h-10 w-10 place-items-center rounded bg-slate-100 text-lg">{KIND_ICON[a.kind]}</span>}
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-slate-800" title={a.filename}>{a.filename}</p>
          <p className="text-slate-500">
            {a.status === "ready" ? summary(a) : a.status === "failed" ? <span className="text-red-700">{a.error}</span>
              : <span className="inline-flex items-center gap-1"><Spinner small /> {a.kind === "audio" ? "transcribing" : a.kind === "image" ? "reading text" : "extracting"}…</span>}
          </p>
        </div>
        {onDelete && <button type="button" onClick={onDelete} aria-label={`Remove ${a.filename}`} className="text-slate-400 hover:text-red-600">✕</button>}
      </div>
      {ready && q.data?.text && (
        <div className="border-t border-slate-100 px-2 py-1.5">
          <button type="button" className="text-indigo-700 hover:underline" onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Show"} {a.kind === "audio" ? "transcript" : "extracted text"}
          </button>
          {open && <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap font-sans text-slate-700">{q.data.text}</pre>}
        </div>
      )}
    </div>
  );
}

function summary(a: Attachment): string {
  const m = a.meta as Record<string, number | string | null>;
  if (a.kind === "audio") return `${Math.round(Number(m.duration_seconds ?? 0))} s · transcribed`;
  if (a.kind === "image") return `${m.ocr_lines ?? 0} lines of text · ${bytes(a.size_bytes)}`;
  if (m.pages) return `${m.pages} page${m.pages === 1 ? "" : "s"}${Number(m.ocr_pages) ? ` · ${m.ocr_pages} OCR` : ""}`;
  return bytes(a.size_bytes);
}

// ------------------------------------------------------------------ voice
export type RecorderState = "idle" | "recording" | "unsupported";

/** Microphone recording with MediaRecorder; resolves to a WebM/Ogg blob. */
export function useRecorder(maxSeconds = 120) {
  const [state, setState] = useState<RecorderState>(
    typeof navigator !== "undefined" && navigator.mediaDevices && typeof MediaRecorder !== "undefined" ? "idle" : "unsupported",
  );
  const [seconds, setSeconds] = useState(0);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const done = useRef<(b: Blob) => void>(() => {});
  const timer = useRef<number | undefined>(undefined);

  const stop = useCallback(() => {
    if (rec.current && rec.current.state !== "inactive") rec.current.stop();
  }, []);

  const start = useCallback(async (onDone: (b: Blob) => void) => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const r = new MediaRecorder(stream);
    chunks.current = [];
    done.current = onDone;
    r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    r.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      window.clearInterval(timer.current);
      setState("idle");
      done.current(new Blob(chunks.current, { type: r.mimeType || "audio/webm" }));
    };
    rec.current = r;
    r.start();
    setSeconds(0);
    setState("recording");
    const began = Date.now();
    timer.current = window.setInterval(() => {
      const s = Math.floor((Date.now() - began) / 1000);
      setSeconds(s);
      if (s >= maxSeconds) r.stop();
    }, 250);
  }, [maxSeconds]);

  return { state, seconds, start, stop };
}

let current: HTMLAudioElement | null = null;

/** Play a reply aloud (Piper TTS on the server; cached after the first request). */
export async function speak(messageId: string): Promise<void> {
  const blob = await api<Blob>(`/messages/${messageId}/speech`, { raw: true });
  current?.pause();
  const url = URL.createObjectURL(blob);
  current = new Audio(url);
  current.onended = () => URL.revokeObjectURL(url);
  await current.play();
}

export function SpeakButton({ messageId }: { messageId: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <button type="button" disabled={busy} aria-label="Read this reply aloud"
      onClick={() => { setBusy(true); speak(messageId).catch(() => undefined).finally(() => setBusy(false)); }}
      className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-indigo-700 disabled:opacity-50">
      {busy ? <Spinner small /> : <span aria-hidden>🔊</span>} Listen
    </button>
  );
}

export function ProviderTag({ provider }: { provider: string | null }) {
  if (!provider || provider === "tool") return null;
  const claude = provider.startsWith("claude");
  return (
    <span title={claude ? "Answered by Claude" : "Answered by local models on this server"}
      className={clsx("rounded px-1.5 py-0.5 text-[11px] font-medium", claude ? "bg-violet-50 text-violet-700" : "bg-slate-100 text-slate-600")}>
      {claude ? `Claude · ${provider.replace("claude:", "")}` : provider === "local" ? "Local" : provider}
    </span>
  );
}

