// Sidecar HTTP client: base URL + one-time token come from the Tauri shell.
import type { Taxonomy } from "./contracts";

export interface SidecarInfo {
  port: number;
  token: string;
}

export interface Task {
  id: string;
  source_type: string;
  source_url: string | null;
  file_path: string;
  file_hash: string | null;
  status: string;
  stage: string;
  error_stage: string | null;
  error_msg: string | null;
  result_type: string | null;
  confidence: number | null;
  created_at: string;
  updated_at: string;
  source_meta: { duration?: number; has_audio?: boolean } | null;
}

export interface ClassifyResult {
  file: string;
  type: string;
  type_name: string;
  probabilities: Record<string, number>;
  confidence: number;
  analysis_worthy: boolean;
  summary_zh: string;
  observations: string[];
  free_note: string | null;
  other_guess: string | null;
  l2_triggered: boolean;
  l2_transcript_excerpt: string | null;
  l2_audio_note: string | null;
  decision_layer: string;
  degraded: boolean;
  timings_s: Record<string, number>;
}

export interface FrameRef {
  index: number;
  url: string;
  name: string;
}

interface TemplateLite {
  id: string;
  name: string;
  builtin: boolean;
  user_layer: string;
}

export interface ReportView {
  id: string;
  report_json: {
    title: string;
    summary: string;
    chapters: { title: string; start: number; end: number; summary: string }[];
    viewpoints: { topic: string; claims: { text: string; time: number }[] }[];
    todos: { text: string; time?: number; context?: string }[];
    quotes: { text: string; time: number }[];
    entities?: string[];
    qa?: { q: string; a: string }[];
    meta?: Record<string, unknown>;
  };
  markdown: string;
  model: string;
  created_at?: string;
}

export interface Stats {
  total: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
}

export interface SettingsView {
  configured: { dashscope: boolean; typesafe: boolean; douyin: boolean } | Record<string, never>;
  params?: Record<string, unknown>;
  stored?: Record<string, string>;
  error?: string;
}

let base = "";
let token = "";

export function configureSidecar(info: SidecarInfo) {
  base = `http://127.0.0.1:${info.port}`;
  token = info.token;
}

export function sidecarConfigured() {
  return base !== "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Auth-Token": token,
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* keep status */
    }
    throw new Error(detail);
  }
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export const api = {
  health: () => request<{ ok: boolean }>("/health"),
  listTasks: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request<Task[]>(`/tasks${qs ? `?${qs}` : ""}`);
  },
  getTask: (id: string) => request<Task>(`/tasks/${id}`),
  getResult: (id: string) => request<ClassifyResult>(`/tasks/${id}/result`),
  listFrames: (id: string) => request<FrameRef[]>(`/tasks/${id}/frames`),
  importLocal: (paths: string[]) =>
    request<{ created: Task[]; missing: string[] }>("/tasks/local", {
      method: "POST",
      body: JSON.stringify({ paths }),
    }),
  importDouyin: (links: string[]) =>
    request<{ created: Task[] }>("/tasks/douyin", {
      method: "POST",
      body: JSON.stringify({ links }),
    }),
  checkDouyinCookie: () => request<{ status: string; reason: string }>("/douyin/check-cookie"),
  cancel: (id: string) => request<Task>(`/tasks/${id}/cancel`, { method: "POST" }),
  analyze: (id: string, body: { template_id?: string; instructions?: string; override?: boolean }) =>
    request<Task>(`/tasks/${id}/analyze`, { method: "POST", body: JSON.stringify(body) }),
  getReport: (id: string) => request<ReportView>(`/tasks/${id}/report`),
  templates: () => request<TemplateLite[]>(`/templates`),
  retry: (id: string) => request<Task>(`/tasks/${id}/retry`, { method: "POST" }),
  reclassify: (id: string) =>
    request<Task>(`/tasks/${id}/reclassify`, { method: "POST" }),
  remove: (id: string) => request<{ deleted: string }>(`/tasks/${id}`, { method: "DELETE" }),
  stats: () => request<Stats>("/stats"),
  taxonomy: () => request<Taxonomy>("/taxonomy"),
  putTaxonomy: (tax: Taxonomy) =>
    request<Taxonomy>("/taxonomy", { method: "PUT", body: JSON.stringify(tax) }),
  resetTaxonomy: () => request<Taxonomy>("/taxonomy/reset", { method: "POST" }),
  settings: () => request<SettingsView>("/settings"),
  putParams: (params: Record<string, string>) =>
    request<{ ok: boolean }>("/settings/params", {
      method: "PUT",
      body: JSON.stringify(params),
    }),
  putKeys: (keys: Record<string, string>) =>
    request<{ ok: boolean; written: string[] }>("/settings/keys", {
      method: "PUT",
      body: JSON.stringify(keys),
    }),
  csvUrl: () => `${base}/export/tasks.csv?token=${encodeURIComponent(token)}`,
  mediaUrl: (path: string) => `${base}${path}`,
};

export function taskEventsUrl(id: string): string {
  return `${base}/tasks/${id}/events?token=${encodeURIComponent(token)}`;
}
