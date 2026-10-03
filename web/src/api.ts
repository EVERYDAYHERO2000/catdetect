export type Point = [number, number];
export type Box = [number, number, number, number];
export type Species = "cat" | "dog";

export interface Direction {
  line: [Point, Point];
  door_point: Point;
  margin: number;
}

export interface Nvr {
  id: number;
  name: string;
  host: string;
  http_port: number;
  rtsp_port: number;
  https: boolean;
  username: string;
  has_password: boolean;
  event_codes: string[];
  enabled: boolean;
  events_connected?: boolean | null;
}

export interface Camera {
  id: number;
  slug: string;
  name: string;
  nvr_id: number | null;
  channel: number;
  stream: "main" | "sub";
  source_url: string | null;
  trigger: "motion" | "always";
  fps: number;
  linger: number;
  clear_after: number;
  species: Species[];
  zone: Point[] | null;
  direction: Direction | null;
  min_conf: number;
  confirm_hits: number;
  confirm_conf: number;
  identity_conf: number;
  save_frames: boolean;
  enabled: boolean;
}

export interface Identity {
  id: number;
  name: string;
  species: Species;
  is_own: boolean;
  notes: string;
  samples: number;
}

export interface Prediction {
  box: Box;
  species: Species;
  conf?: number;
  identity_id: number | null;
  identity_conf?: number;
}

export interface AnnotationItem {
  id?: number;
  box: Box;
  species: Species;
  identity_id: number | null;
}

export interface ImageItem {
  id: number;
  camera_id: number | null;
  path: string;
  width: number;
  height: number;
  captured_at: string;
  source: string;
  status: "unlabeled" | "labeled" | "skipped";
  predictions: Prediction[];
  annotations?: AnnotationItem[];
  prev_id?: number | null;
  next_id?: number | null;
}

export interface EventItem {
  id: number;
  camera_id: number;
  ts: string;
  kind: "seen" | "arrived" | "left";
  species: Species;
  identity_id: number | null;
  confidence: number;
  identity_confidence: number | null;
  track_id: number;
  has_snapshot: boolean;
}

export interface CameraState {
  online: boolean;
  motion: boolean;
  species: Record<string, { present: boolean; at_door: boolean }>;
  last_event: EventItem | null;
}

export interface IdentityState {
  present: boolean;
  at_door: boolean;
  last_direction: "arrived" | "left" | null;
  last_camera_id: number | null;
  last_seen: number | null;
}

export interface FullState {
  service: { version: string; detector: Record<string, unknown> };
  cameras: { id: number; slug: string; name: string; species: Species[]; enabled: boolean; has_direction: boolean; state: CameraState | null }[];
  identities: { id: number; name: string; species: Species; is_own: boolean; state: IdentityState | null }[];
}

export interface TrainingJob {
  id: number;
  kind: "detector" | "classifier";
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  params: Record<string, unknown> & { last_metrics?: Record<string, number> };
  progress: number;
  message: string;
  model_id: number | null;
  log?: string;
}

export interface MlModel {
  id: number;
  kind: "detector" | "classifier";
  name: string;
  path: string;
  created_at: string;
  metrics: Record<string, number>;
  classes: Record<string, number>;
  active: boolean;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d.map((e) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`).join("; ");
    }
  }
  return fallback;
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const init: RequestInit = { method: opts.method ?? (opts.body || opts.form ? "POST" : "GET"), credentials: "same-origin" };
  if (opts.form) {
    init.body = opts.form;
  } else if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, init);
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) {
    if (r.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("catdetect:unauthorized"));
    throw new ApiError(r.status, errorMessage(data, `Ошибка ${r.status}`));
  }
  return data as T;
}

export const SPECIES_LABEL: Record<Species, string> = { cat: "Кошка", dog: "Собака" };
export const EVENT_LABEL: Record<EventItem["kind"], string> = { seen: "замечен", arrived: "пришёл к двери", left: "ушёл от двери" };

export function fmtTime(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined) return "—";
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
