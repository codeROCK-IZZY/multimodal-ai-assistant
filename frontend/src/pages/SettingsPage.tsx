import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { api, download } from "../api/client";
import type { Permission, Settings, SystemInfo, ToolInfo } from "../api/types";
import { toolLabel } from "../components/chat";
import { Badge, Button, Card, ErrorBox, Loading, PageHeader } from "../components/ui";
import { useAuth } from "../lib/auth";

const PERMS: { value: Permission; label: string; hint: string }[] = [
  { value: "allow", label: "Allow", hint: "runs without asking" },
  { value: "ask", label: "Ask", hint: "asks you to confirm first" },
  { value: "deny", label: "Off", hint: "never runs" },
];

function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
      className={clsx("relative h-6 w-11 shrink-0 rounded-full transition disabled:opacity-50", checked ? "bg-indigo-600" : "bg-slate-300")}>
      <span className={clsx("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition", checked ? "left-5" : "left-0.5")} />
    </button>
  );
}

export function SettingsPage() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/settings") });
  const tools = useQuery({ queryKey: ["tools"], queryFn: () => api<ToolInfo[]>("/tools"), staleTime: Infinity });
  const info = useQuery({ queryKey: ["system-info"], queryFn: () => api<SystemInfo>("/system/info"), staleTime: Infinity });
  const save = useMutation({
    mutationFn: (body: Partial<Settings>) => api<Settings>("/settings", { method: "PUT", body }),
    onSuccess: (s) => qc.setQueryData(["settings"], s),
  });
  if (settings.isLoading || tools.isLoading) return <Loading />;
  const s = settings.data;
  const member = can("member");

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-6">
      <PageHeader title="Settings" subtitle="Control what the assistant remembers, how it replies and which tools it may use." />
      <ErrorBox error={settings.error ?? save.error} />
      {s && (
        <Card title="Memory and voice">
          <div className="space-y-4 text-sm">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-medium text-slate-800">Remember things I tell it</p>
                <p className="text-slate-500">When off, nothing new is saved and stored memories are not used in answers.</p>
              </div>
              <Toggle label="Memory" checked={s.memory_enabled} disabled={!member} onChange={(v) => save.mutate({ memory_enabled: v })} />
            </div>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-medium text-slate-800">Read replies aloud</p>
                <p className="text-slate-500">Plays each reply with the local voice ({info.data?.text_to_speech ?? "Piper"}).</p>
              </div>
              <Toggle label="Speak replies" checked={s.speak_replies} disabled={!member} onChange={(v) => save.mutate({ speak_replies: v })} />
            </div>
          </div>
        </Card>
      )}
      {s && tools.data && (
        <Card title="Tool permissions">
          <p className="mb-3 text-sm text-slate-500">
            Tools that change your data ask first by default. When a conversation contains files, every data-changing tool asks
            first — a file could contain instructions meant to trick the assistant.
          </p>
          <ul className="divide-y divide-slate-100">
            {tools.data.map((t) => (
              <li key={t.name} className="flex flex-wrap items-center gap-3 py-3 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-slate-800">
                    {toolLabel(t.name)} {t.changes_data && <Badge tone="amber">changes your data</Badge>}
                  </p>
                  <p className="text-slate-500">{t.description}</p>
                </div>
                <div role="radiogroup" aria-label={`${toolLabel(t.name)} permission`} className="flex overflow-hidden rounded-lg border border-slate-300">
                  {PERMS.map((p) => (
                    <button key={p.value} type="button" role="radio" aria-checked={s.tool_permissions[t.name] === p.value}
                      title={p.hint} disabled={!member}
                      onClick={() => save.mutate({ tool_permissions: { [t.name]: p.value } })}
                      className={clsx("px-3 py-1 text-xs", s.tool_permissions[t.name] === p.value
                        ? "bg-indigo-600 text-white" : "bg-white text-slate-600 hover:bg-slate-50")}>
                      {p.label}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card title="Your data">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <p className="flex-1 text-slate-600">
            {info.data?.data_leaves_server
              ? "Messages and file contents are sent to the Claude API to generate answers."
              : "Everything — speech recognition, speech synthesis, reading images and answering — runs on this server."}
          </p>
          <Button variant="secondary" onClick={() => void download("/me/export", "my-assistant-data.json")}>Export my data</Button>
        </div>
      </Card>
    </div>
  );
}
