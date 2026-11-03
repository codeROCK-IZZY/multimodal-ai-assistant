import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Memory, Settings, Task } from "../api/types";
import { Badge, Button, Card, Empty, ErrorBox, Input, Loading, PageHeader } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

export function MemoryPage() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const memories = useQuery({ queryKey: ["memories"], queryFn: () => api<Memory[]>("/memories") });
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api<Task[]>("/tasks?include_done=true") });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/settings") });
  const [fact, setFact] = useState("");
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const inv = (k: string) => () => void qc.invalidateQueries({ queryKey: [k] });

  const addMemory = useMutation({ mutationFn: (text: string) => api("/memories", { body: { text } }), onSuccess: inv("memories") });
  const delMemory = useMutation({ mutationFn: (id: string) => api(`/memories/${id}`, { method: "DELETE" }), onSuccess: inv("memories") });
  const clear = useMutation({ mutationFn: () => api("/memories", { method: "DELETE" }), onSuccess: inv("memories") });
  const addTask = useMutation({
    mutationFn: () => api("/tasks", { body: { title, due: due || null } }),
    onSuccess: () => { setTitle(""); setDue(""); inv("tasks")(); },
  });
  const toggle = useMutation({
    mutationFn: (t: Task) => api(`/tasks/${t.id}`, { method: "PATCH", body: { done: !t.done } }), onSuccess: inv("tasks"),
  });
  const delTask = useMutation({ mutationFn: (id: string) => api(`/tasks/${id}`, { method: "DELETE" }), onSuccess: inv("tasks") });

  const submitFact = (e: FormEvent) => {
    e.preventDefault();
    if (fact.trim()) addMemory.mutate(fact.trim(), { onSuccess: () => setFact("") });
  };
  const member = can("member");

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-6">
      <PageHeader title="Memory & tasks"
        subtitle="Everything the assistant remembers about you and your task list. You can edit or delete any of it." />
      {settings.data && !settings.data.memory_enabled && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          Memory is turned off in Settings: nothing new is saved and stored memories aren't used in answers.
        </p>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="What I remember" actions={member && (memories.data?.length ?? 0) > 0 && (
          <Button variant="secondary" onClick={() => confirm("Forget everything?") && clear.mutate()}>Forget all</Button>
        )}>
          {member && (
            <form onSubmit={submitFact} className="mb-4 flex gap-2">
              <Input placeholder="e.g. My manager is Dana Whitfield" value={fact} onChange={(e) => setFact(e.target.value)} aria-label="New memory" />
              <Button type="submit" busy={addMemory.isPending}>Add</Button>
            </form>
          )}
          {memories.isLoading ? <Loading /> : memories.data?.length ? (
            <ul className="divide-y divide-slate-100">
              {memories.data.map((m) => (
                <li key={m.id} className="flex items-start gap-3 py-2.5 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="text-slate-800">{m.text}</p>
                    <p className="text-xs text-slate-500">{m.source === "chat" ? "from a conversation" : "added by you"} · {dateTime(m.created_at)}</p>
                  </div>
                  {member && <button className="text-xs text-red-700 hover:underline" onClick={() => delMemory.mutate(m.id)}>Delete</button>}
                </li>
              ))}
            </ul>
          ) : <Empty>Nothing yet. Say “remember that …” in a conversation.</Empty>}
          <ErrorBox error={memories.error ?? addMemory.error} />
        </Card>

        <Card title="Tasks">
          {member && (
            <form onSubmit={(e) => { e.preventDefault(); if (title.trim()) addTask.mutate(); }} className="mb-4 flex gap-2">
              <Input placeholder="New task" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="New task" />
              <Input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due date" className="w-40" />
              <Button type="submit" busy={addTask.isPending}>Add</Button>
            </form>
          )}
          {tasks.isLoading ? <Loading /> : tasks.data?.length ? (
            <ul className="divide-y divide-slate-100">
              {tasks.data.map((t) => (
                <li key={t.id} className="flex items-center gap-3 py-2.5 text-sm">
                  <input type="checkbox" checked={t.done} disabled={!member} onChange={() => toggle.mutate(t)}
                    aria-label={`Mark ${t.title} ${t.done ? "not done" : "done"}`} className="h-4 w-4 accent-indigo-600" />
                  <span className={t.done ? "flex-1 text-slate-400 line-through" : "flex-1 text-slate-800"}>{t.title}</span>
                  {t.due && <Badge tone={!t.done && t.due < new Date().toISOString().slice(0, 10) ? "red" : "slate"}>due {t.due}</Badge>}
                  {member && <button className="text-xs text-red-700 hover:underline" onClick={() => delTask.mutate(t.id)}>Delete</button>}
                </li>
              ))}
            </ul>
          ) : <Empty>No tasks. Try “remind me to … on friday”.</Empty>}
          <ErrorBox error={tasks.error ?? addTask.error} />
        </Card>
      </div>
    </div>
  );
}
