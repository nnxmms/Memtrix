// Typed API client for the Memtrix Control Panel.

export interface ValidateResponse {
  valid: boolean;
  errors: string[];
}

export interface TestResult {
  ok: boolean;
  detail: string;
}

export interface ModelDiscoveryResult {
  ok: boolean;
  models: string[];
  detail: string;
}

export interface SecretInfo {
  key: string;
  value: string;
  backend: string;
}

export interface SecretListResponse {
  backend: string;
  secrets: SecretInfo[];
}

export interface Conclusion {
  id: string;
  content: string;
  peer: string;
  kind: string;
  premises: string[];
  times_seen: number;
  ts: number;
  source: string;
}

export interface PeerCard {
  peer: string;
  text: string;
  max_chars: number;
  frozen: boolean;
}

export interface PeerSummary {
  peer: string;
  count: number;
  card_chars: number;
  frozen: boolean;
}

export interface DeriverState {
  paused: boolean;
}

export interface PersonSummary {
  slug: string;
  name: string;
  type: string;
  relation: string;
  facts: number;
  card_chars: number;
}

export interface PersonCard {
  slug: string;
  name: string;
  type: string;
  relation: string;
  card: string;
  facts: Conclusion[];
}

export interface MemoryEvent {
  id: string;
  title: string;
  date: string;
  time_of_day: string;
  entities: string[];
  entity_names: string[];
  location: string;
  status: string;
  recurring: boolean;
  reviewed: boolean;
  source: string;
}

export interface StatusResponse {
  version: string;
  agent_alive: boolean;
  heartbeat: number | null;
  deriver_paused: boolean;
  restart_requested: boolean;
  memory_count: number;
}

export type Config = Record<string, any>;

const TOKEN_KEY = "memtrix_web_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  errors: string[];
  constructor(status: number, message: string, errors: string[] = []) {
    super(message);
    this.status = status;
    this.errors = errors;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["X-Memtrix-Token"] = token;

  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let detail: any = null;
    try {
      detail = await res.json();
    } catch {
      /* ignore */
    }
    const errors: string[] =
      detail?.detail?.errors ??
      (typeof detail?.detail === "string" ? [detail.detail] : []);
    const message = errors[0] ?? `Request failed (${res.status})`;
    throw new ApiError(res.status, message, errors);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  // status & lifecycle
  status: () => request<StatusResponse>("GET", "/api/status"),
  restart: () => request<{ message: string }>("POST", "/api/restart"),

  // config
  getConfig: () => request<Config>("GET", "/api/config"),
  validateConfig: (config: Config) =>
    request<ValidateResponse>("POST", "/api/config/validate", { config }),
  putConfig: (config: Config) =>
    request<{ message: string }>("PUT", "/api/config", { config }),
  testProvider: (type: string, params: Record<string, any>) =>
    request<TestResult>("POST", "/api/config/test/provider", { type, params }),
  testChannel: (type: string, params: Record<string, any>) =>
    request<TestResult>("POST", "/api/config/test/channel", { type, params }),
  testEmail: (params: Record<string, any>) =>
    request<TestResult>("POST", "/api/config/test/email", { type: "email", params }),
  discoverModels: (type: string, params: Record<string, any>) =>
    request<ModelDiscoveryResult>("POST", "/api/config/discover/models", {
      type,
      params,
    }),

  // secrets
  listSecrets: () => request<SecretListResponse>("GET", "/api/secrets"),
  setSecret: (key: string, value: string, note = "") =>
    request<{ message: string }>("PUT", `/api/secrets/${encodeURIComponent(key)}`, {
      value,
      note,
    }),

  // memory
  listPeers: () => request<PeerSummary[]>("GET", "/api/memory/peers"),
  listConclusions: (params: {
    peer?: string;
    kinds?: string[];
    q?: string;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.peer) qs.set("peer", params.peer);
    if (params.q) qs.set("q", params.q);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    (params.kinds ?? []).forEach((k) => qs.append("kinds", k));
    return request<Conclusion[]>("GET", `/api/memory/conclusions?${qs.toString()}`);
  },
  addConclusion: (body: {
    peer: string;
    kind: string;
    content: string;
    premises: string[];
  }) => request<Conclusion>("POST", "/api/memory/conclusions", body),
  updateConclusion: (
    id: string,
    body: { content?: string; kind?: string; premises?: string[] }
  ) => request<Conclusion>("PATCH", `/api/memory/conclusions/${id}`, body),
  deleteConclusion: (id: string) =>
    request<{ message: string }>("DELETE", `/api/memory/conclusions/${id}`),
  wipePeer: (peer: string) =>
    request<{ message: string }>("DELETE", `/api/memory/peers/${peer}/conclusions`),
  getCard: (peer: string) => request<PeerCard>("GET", `/api/memory/peers/${peer}/card`),
  putCard: (peer: string, text: string) =>
    request<{ message: string }>("PUT", `/api/memory/peers/${peer}/card`, { text }),
  setFreeze: (peer: string, frozen: boolean) =>
    request<{ message: string }>("PUT", `/api/memory/peers/${peer}/freeze`, { frozen }),
  exportMemory: (peer?: string) =>
    request<Conclusion[]>(
      "GET",
      `/api/memory/export${peer ? `?peer=${peer}` : ""}`
    ),
  importMemory: (records: unknown[]) =>
    request<{ message: string }>("POST", "/api/memory/import", { records }),
  getDeriver: () => request<DeriverState>("GET", "/api/memory/deriver"),
  setDeriver: (paused: boolean) =>
    request<DeriverState>("PUT", "/api/memory/deriver", { paused }),

  // people (entities) & events
  listPeople: () => request<PersonSummary[]>("GET", "/api/memory/people"),
  getPerson: (slug: string) =>
    request<PersonCard>("GET", `/api/memory/people/${encodeURIComponent(slug)}`),
  deletePerson: (slug: string) =>
    request<{ message: string }>("DELETE", `/api/memory/people/${encodeURIComponent(slug)}`),
  listEvents: () => request<MemoryEvent[]>("GET", "/api/memory/events"),
  addEvent: (body: {
    title: string;
    date: string;
    time_of_day?: string;
    location?: string;
    entities?: string[];
    recurring?: boolean;
  }) => request<MemoryEvent>("POST", "/api/memory/events", body),
  deleteEvent: (id: string) =>
    request<{ message: string }>("DELETE", `/api/memory/events/${id}`),
  wipeEvents: () => request<{ message: string }>("DELETE", "/api/memory/events"),
};

// Subscribe to restart progress via Server-Sent Events.
export function streamRestart(
  onEvent: (phase: string, detail: string) => void,
  onDone: () => void
): () => void {
  const source = new EventSource("/api/restart/stream");
  source.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data.phase, data.detail);
      if (data.phase === "ready" || data.phase === "timeout") {
        source.close();
        onDone();
      }
    } catch {
      /* ignore malformed event */
    }
  };
  source.onerror = () => {
    source.close();
    onDone();
  };
  return () => source.close();
}
