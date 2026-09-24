/** Thin API client. The SPA is served by the same FastAPI process, so every
 *  path is relative — no host config, and nothing to break when offline. */

export type StepState =
  | "pending" | "active" | "complete" | "skipped"
  | "out_of_order" | "unverified" | "overridden" | "stalled";

export interface Step {
  id: string;
  name: string;
  voice: string;
  state: StepState;
  confidence: number;
}

export interface Alert {
  kind: string;
  message: string;
  severity: string;
  seq: number;
}

export interface SessionStats {
  id: string;
  records: number;
  bytes: number;
  frames: number;
  video_bytes: number;
  rtsp: boolean;
  closed: boolean;
}

export interface LiveState {
  procedure: string;
  mode: string;
  open_vocab: boolean;
  fps: number;
  session: SessionStats | null;
  steps: Step[];
  next: { id: string; name: string; voice: string } | null;
  alert: Alert | null;
  complete: boolean;
  summary: {
    total: number; complete: number; skipped: number;
    out_of_order: number; unverified: number; alerts: number; duration: number;
  };
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export const api = {
  skip: () => fetch("/api/skip", { method: "POST" }),
  restart: (mode: "clean" | "strict" = "clean") =>
    fetch(`/api/restart?mode=${mode}`, { method: "POST" }),

  classes: () => fetch("/api/classes").then(j<string[]>),
  build: (sequence: string[], name?: string) =>
    fetch("/api/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sequence, name }),
    }).then((r) => r.json()),

  // ---- training
  trainClasses: () =>
    fetch("/api/train/classes").then(
      j<{ classes: { name: string; count: number; background: boolean }[];
          status: TrainStatus; model_ready: boolean }>
    ),
  addClass: (name: string) => {
    const fd = new FormData();
    fd.append("name", name);
    return fetch("/api/train/class", { method: "POST", body: fd });
  },
  delClass: (name: string) =>
    fetch(`/api/train/class?name=${encodeURIComponent(name)}`, { method: "DELETE" }),
  capture: (name: string) =>
    fetch(`/api/train/capture?name=${encodeURIComponent(name)}`, { method: "POST" })
      .then((r) => r.json()),
  upload: (name: string, files: FileList) => {
    const fd = new FormData();
    fd.append("name", name);
    for (const f of Array.from(files)) fd.append("files", f);
    return fetch("/api/train/upload", { method: "POST", body: fd });
  },
  uploadVideo: (name: string, file: File, frames = 40) => {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("frames", String(frames));
    fd.append("file", file);
    return fetch("/api/train/video", { method: "POST", body: fd }).then((r) => r.json());
  },
  startTrain: (epochs: number) =>
    fetch(`/api/train/start?epochs=${epochs}`, { method: "POST" }).then((r) => r.json()),
  trainStatus: () =>
    fetch("/api/train/status").then(j<{ status: TrainStatus; model_ready: boolean }>),
  useModel: () => fetch("/api/train/use", { method: "POST" }).then((r) => r.json()),

  // ---- sessions
  sessions: () => fetch("/api/sessions").then(j<SessionRow[]>),
  session: (id: string) => fetch(`/api/sessions/${id}`).then(j<SessionDetail>),
  verify: (id: string) =>
    fetch(`/api/sessions/${id}/verify`).then(
      j<{ ok: boolean; record_count: number; first_bad_seq: number | null; error: string | null }>
    ),
};

export interface TrainStatus {
  state: "idle" | "training" | "done" | "error";
  message: string;
  epoch: number;
  epochs: number;
}

export interface SessionRow {
  id: string;
  procedure_id: string;
  status: string;
  started_at: string;
  duration_ms: number | null;
  steps_total: number | null;
  steps_complete: number | null;
  steps_skipped: number | null;
  steps_out_of_order: number | null;
  alert_count: number | null;
  session_dir: string;
}

export interface SessionDetail {
  session: SessionRow;
  steps: { step_id: string; ordinal: number; state: string; confidence: number | null }[];
  alerts: { kind: string; severity: string; message: string; raised_at: string }[];
  chain: {
    record_count: number; bytes_written: number; last_hash: string;
    verified_ok: number | null; first_bad_seq: number | null;
  } | null;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
