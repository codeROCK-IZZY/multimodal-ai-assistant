import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Attachment, Conversation, ConversationDetail, Exchange, Message, Settings, SystemInfo } from "../api/types";
import {
  AttachmentChip, Citations, ProviderTag, RichText, SpeakButton, ToolEvents, ToolRequestCard, speak, useRecorder,
} from "../components/chat";
import { ErrorBox, Spinner } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

const SUGGESTIONS = [
  "What was the total on the receipt?",
  "Summarize the document",
  "Remind me to submit the expense claim on friday",
  "Convert 5 km to miles",
];

export function ChatPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const conversations = useQuery({ queryKey: ["conversations"], queryFn: () => api<Conversation[]>("/conversations") });

  const create = useMutation({
    mutationFn: () => api<Conversation>("/conversations", { body: {} }),
    onSuccess: (c) => {
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      nav(`/c/${c.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: (cid: string) => api(`/conversations/${cid}`, { method: "DELETE" }),
    onSuccess: (_, cid) => {
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      if (cid === id) nav("/");
    },
  });

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-200 bg-slate-50 md:flex">
        <div className="p-3">
          {can("member") && (
            <button onClick={() => create.mutate()} disabled={create.isPending}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 shadow-sm hover:bg-slate-100">
              + New conversation
            </button>
          )}
        </div>
        <nav aria-label="Conversations" className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {conversations.data?.map((c) => (
            <div key={c.id} className={clsx("group flex items-center rounded-lg", c.id === id ? "bg-indigo-100" : "hover:bg-slate-200/60")}>
              <button onClick={() => nav(`/c/${c.id}`)} className="min-w-0 flex-1 px-3 py-2 text-left">
                <p className={clsx("truncate text-sm", c.id === id ? "font-medium text-indigo-900" : "text-slate-700")}>{c.title}</p>
                <p className="text-[11px] text-slate-500">{dateTime(c.updated_at)}</p>
              </button>
              {can("member") && (
                <button aria-label={`Delete ${c.title}`} onClick={() => confirm("Delete this conversation and its files?") && remove.mutate(c.id)}
                  className="mr-2 hidden text-slate-400 hover:text-red-600 group-hover:block">✕</button>
              )}
            </div>
          ))}
          {conversations.data?.length === 0 && <p className="px-3 py-2 text-xs text-slate-500">No conversations yet.</p>}
        </nav>
      </aside>
      {id ? <Thread key={id} id={id} /> : <Welcome onStart={() => create.mutate()} canStart={can("member")} />}
    </div>
  );
}

function Welcome({ onStart, canStart }: { onStart: () => void; canStart: boolean }) {
  const info = useQuery({ queryKey: ["system-info"], queryFn: () => api<SystemInfo>("/system/info"), staleTime: Infinity });
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="max-w-lg text-center">
        <h1 className="text-2xl font-semibold text-slate-900">What can I help with?</h1>
        <p className="mt-2 text-sm text-slate-600">
          Type or speak. Attach photos, scans, PDFs, Word documents or recordings — answers cite the page, line or
          timestamp they came from.
        </p>
        {canStart && (
          <button onClick={onStart} className="mt-5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            Start a conversation
          </button>
        )}
        {info.data && (
          <dl className="mt-8 grid grid-cols-2 gap-3 text-left text-xs">
            {[["Speech → text", info.data.speech_to_text], ["Text → speech", info.data.text_to_speech],
              ["Reading images", info.data.ocr], ["Answers", info.data.llm_mode === "claude" ? `Claude (${info.data.llm_model})` : "local, extractive"]]
              .map(([k, v]) => (
                <div key={k} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
                  <dt className="text-slate-500">{k}</dt><dd className="font-medium text-slate-800">{v}</dd>
                </div>
              ))}
          </dl>
        )}
      </div>
    </div>
  );
}

function Thread({ id }: { id: string }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const key = ["conversation", id];
  const conv = useQuery({ queryKey: key, queryFn: () => api<ConversationDetail>(`/conversations/${id}`) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/settings") });
  const [text, setText] = useState("");
  const [activeCite, setActiveCite] = useState<Record<string, number | null>>({});
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const recorder = useRecorder(120);
  const refresh = () => void qc.invalidateQueries({ queryKey: key });

  const afterReply = (ex: Exchange) => {
    void qc.invalidateQueries({ queryKey: ["conversations"] });
    refresh();
    if (settings.data?.speak_replies) void speak(ex.reply.id).catch(() => undefined);
  };
  const send = useMutation({
    mutationFn: (t: string) => api<Exchange>(`/conversations/${id}/messages`, { body: { text: t } }),
    onSuccess: afterReply,
  });
  const voice = useMutation({
    mutationFn: (blob: Blob) => {
      const form = new FormData();
      form.append("audio", blob, blob.type.includes("ogg") ? "voice.ogg" : "voice.webm");
      return api<Exchange>(`/conversations/${id}/voice`, { form });
    },
    onSuccess: afterReply,
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<Attachment>(`/conversations/${id}/attachments`, { form });
    },
    onSuccess: refresh,
  });
  const removeAttachment = useMutation({
    mutationFn: (aid: string) => api(`/attachments/${aid}`, { method: "DELETE" }), onSuccess: refresh,
  });
  const decide = useMutation({
    mutationFn: ({ rid, approve }: { rid: string; approve: boolean }) =>
      api<Message>(`/tool-requests/${rid}/${approve ? "approve" : "decline"}`, { method: "POST" }),
    onSuccess: () => {
      refresh();
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["memories"] });
    },
  });

  const messages = conv.data?.messages ?? [];
  const busy = send.isPending || voice.isPending;
  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), [messages.length, busy]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    send.mutate(t);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };
  const processing = conv.data?.attachments.some((a) => a.status === "queued" || a.status === "processing");
  const readOnly = !can("member");

  if (conv.isLoading) return <div className="flex flex-1 items-center justify-center"><Spinner /></div>;
  if (conv.error) return <div className="p-6"><ErrorBox error={conv.error} /></div>;

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label={conv.data?.title}>
      {conv.data && conv.data.attachments.length > 0 && (
        <div className="flex gap-2 overflow-x-auto border-b border-slate-200 bg-white px-5 py-2.5" aria-label="Files in this conversation">
          {conv.data.attachments.map((a) => (
            <AttachmentChip key={a.id} attachment={a} onReady={refresh}
              onDelete={readOnly ? undefined : () => removeAttachment.mutate(a.id)} />
          ))}
        </div>
      )}
      <div className="flex-1 overflow-y-auto bg-slate-50 px-4 py-6">
        <div className="mx-auto max-w-3xl space-y-5">
          {messages.length === 0 && (
            <div className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-600">
              <p>Attach a file or ask something. Try:</p>
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => setText(s)} className="rounded-full border border-slate-200 px-3 py-1 text-xs hover:bg-slate-50">{s}</button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} m={m} active={activeCite[m.id] ?? null}
              onCite={(n) => setActiveCite((s) => ({ ...s, [m.id]: n }))} />
          ))}
          {conv.data?.pending_requests.map((r) => (
            <ToolRequestCard key={r.id} request={r} busy={decide.isPending} onDecide={(approve) => decide.mutate({ rid: r.id, approve })} />
          ))}
          {busy && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Spinner small /> {voice.isPending ? "Transcribing and thinking…" : "Thinking…"}
            </div>
          )}
          <ErrorBox error={send.error ?? voice.error ?? upload.error ?? decide.error} />
          <div ref={bottom} />
        </div>
      </div>
      {!readOnly && (
        <form onSubmit={submit} className="border-t border-slate-200 bg-white px-4 py-3">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <input ref={fileInput} type="file" className="hidden" aria-label="Attach a file"
              accept="image/*,.pdf,.docx,.txt,.md,.csv,audio/*,.m4a"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
            <button type="button" onClick={() => fileInput.current?.click()} disabled={upload.isPending}
              title="Attach an image, document or recording" aria-label="Attach file"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50">
              {upload.isPending ? <Spinner small /> : "📎"}
            </button>
            <textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} rows={1}
              placeholder={processing ? "Processing your file…" : "Message the assistant"} aria-label="Message"
              className="max-h-40 min-h-10 flex-1 resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none" />
            {recorder.state !== "unsupported" && (
              <button type="button" disabled={busy}
                onClick={() => (recorder.state === "recording" ? recorder.stop() : void recorder.start((b) => voice.mutate(b)))}
                aria-label={recorder.state === "recording" ? "Stop recording and send" : "Record a voice message"}
                className={clsx("grid h-10 shrink-0 place-items-center rounded-lg px-3 text-sm",
                  recorder.state === "recording" ? "bg-red-600 text-white" : "border border-slate-300 text-slate-700 hover:bg-slate-50")}>
                {recorder.state === "recording" ? `■ ${recorder.seconds}s` : "🎤"}
              </button>
            )}
            <button type="submit" disabled={!text.trim() || busy}
              className="h-10 shrink-0 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
              Send
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function MessageBubble({ m, active, onCite }: { m: Message; active: number | null; onCite: (n: number | null) => void }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2.5 text-sm text-white">
          {m.input_mode === "voice" && <p className="mb-0.5 text-[11px] text-indigo-200">🎤 voice message (transcribed)</p>}
          <p className="whitespace-pre-wrap">{m.content}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="flex">
      <div className={clsx("max-w-[85%] rounded-2xl rounded-bl-sm border bg-white px-4 py-3 text-sm text-slate-800 shadow-sm",
        m.status === "error" ? "border-red-200" : m.status === "not_found" ? "border-amber-200" : "border-slate-200")}>
        <RichText text={m.content} onCite={onCite} />
        <Citations items={m.citations} active={active} onSelect={onCite} />
        <ToolEvents events={m.tool_events} />
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
          <ProviderTag provider={m.provider} />
          {m.latency_ms !== null && <span>{(m.latency_ms / 1000).toFixed(1)} s</span>}
          {typeof m.usage.cost_usd === "number" && <span>${m.usage.cost_usd.toFixed(4)}</span>}
          <SpeakButton messageId={m.id} />
        </div>
      </div>
    </div>
  );
}
