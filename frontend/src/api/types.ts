export type Role = "admin" | "member" | "viewer";
export type Permission = "allow" | "ask" | "deny";

export interface User { id: string; email: string; full_name: string; role: Role; is_active: boolean; created_at: string }

export interface SystemInfo {
  llm_mode: "claude" | "local"; llm_model: string | null; data_leaves_server: boolean;
  speech_to_text: string; text_to_speech: string; ocr: string; embeddings: string;
  max_upload_mb: number; max_audio_seconds: number;
}

export interface Conversation { id: string; title: string; created_at: string; updated_at: string }

export interface Attachment {
  id: string; conversation_id: string | null; kind: "image" | "document" | "audio"; filename: string; mime: string;
  size_bytes: number; status: "queued" | "processing" | "ready" | "failed"; error: string | null;
  meta: Record<string, unknown>; processor: string | null; processing_ms: number | null; created_at: string;
}
export interface AttachmentDetail extends Attachment { text: string }

export interface Citation {
  n: number; source: "chunk" | "memory"; attachment_id: string | null; filename: string | null; locator: string;
  quote: string; score: number | null;
}

export interface ToolEvent {
  name: string; args: Record<string, unknown>; status: string;
  result?: Record<string, unknown> | null; error?: string; reason?: string; request_id?: string;
}

export interface Message {
  id: string; conversation_id: string; role: "user" | "assistant"; content: string; input_mode: "text" | "voice";
  attachment_ids: string[]; citations: Citation[]; tool_events: ToolEvent[];
  status: "answered" | "not_found" | "needs_permission" | "error" | null; provider: string | null;
  usage: Record<string, unknown>; latency_ms: number | null; created_at: string;
}

export interface ToolRequest {
  id: string; conversation_id: string; message_id: string; tool: string; args: Record<string, unknown>;
  status: "pending" | "denied" | "executed" | "failed"; result: Record<string, unknown> | null; created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[]; attachments: Attachment[]; pending_requests: ToolRequest[];
}

export interface Exchange { user_message: Message; reply: Message; pending_requests: ToolRequest[] }

export interface Memory { id: string; text: string; source: "chat" | "manual"; created_at: string }
export interface Task { id: string; title: string; due: string | null; done: boolean; created_at: string }

export interface Settings { memory_enabled: boolean; speak_replies: boolean; tool_permissions: Record<string, Permission> }
export interface ToolInfo { name: string; description: string; default: Permission; changes_data: boolean }

export interface AuditEvent {
  id: number; ts: string; actor: string; action: string; entity: string; entity_id: string | null;
  data: Record<string, unknown>; hash: string;
}
