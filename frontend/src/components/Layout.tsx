import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import type { Role, SystemInfo } from "../api/types";
import { useAuth } from "../lib/auth";

const NAV: { to: string; label: string; role: Role }[] = [
  { to: "/", label: "Assistant", role: "viewer" },
  { to: "/memory", label: "Memory & tasks", role: "viewer" },
  { to: "/settings", label: "Settings", role: "viewer" },
  { to: "/admin", label: "Administration", role: "admin" },
];

export function Layout() {
  const { user, logout, can } = useAuth();
  const info = useQuery({ queryKey: ["system-info"], queryFn: () => api<SystemInfo>("/system/info"), staleTime: Infinity });
  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-slate-200 bg-white px-5 py-2.5">
        <div className="flex items-center gap-2">
          <span aria-hidden className="grid h-7 w-7 place-items-center rounded-lg bg-indigo-600 text-sm font-bold text-white">M</span>
          <span className="text-sm font-semibold text-slate-900">Multimodal Assistant</span>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto">
          {NAV.filter((n) => can(n.role)).map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => clsx("whitespace-nowrap rounded-md px-3 py-1.5 text-sm",
                isActive ? "bg-indigo-50 font-medium text-indigo-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          {info.data && (
            <span title={info.data.data_leaves_server ? "Messages and files are sent to the Claude API" : "Everything runs on this server"}
              className={clsx("hidden rounded-full px-2.5 py-0.5 text-xs font-medium sm:inline",
                info.data.llm_mode === "claude" ? "bg-violet-50 text-violet-700" : "bg-emerald-50 text-emerald-700")}>
              {info.data.llm_mode === "claude" ? `Claude · ${info.data.llm_model}` : "Local models · offline"}
            </span>
          )}
          <span className="text-slate-600">{user?.email}</span>
          <button onClick={() => void logout()} className="text-indigo-700 hover:underline">Sign out</button>
        </div>
      </header>
      <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
