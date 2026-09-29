/** Thin API client. The SPA is served by the same FastAPI process, so every
 *  path is relative - no host config, and nothing to break when offline. */

export type StepState =
  | "pending" | "active" | "complete" | "skipped"
  | "out_of_order" | "unverified" | "overridden" | "stalled";

/** Session lifecycle (docs/03-APP-FLOW.md §3). The camera is off in "ready". */
export type Phase = "ready" | "live" | "complete";

export interface Step {
  id: string;
  name: string;
  voice: string;
  state: StepState;
  confidence: number;
  ordinal: number;
  duration_s: number | null;
  evidence: string[];
  reason: string;
}

/** Which perception subsystems this procedure needs, and whether they work.
 *  Surfaced so a degraded run is visible rather than silently wrong. */
export interface BodyGesture { name: string; side: string; conf: number }

/** Body tracking: pose, hands, what the hands touch, and body actions. */
export interface Body {
  enabled: boolean;
  /** A procedure step needs it, so it cannot be switched off. */
  forced: boolean;
  ready: boolean;
  tracked: boolean;
  gestures: BodyGesture[];
  contacts: { side: string; object: string }[];
}

export interface Perception {
  rack_required: boolean;
  rack_locked: boolean;
  pose_required: boolean;
  pose_ok: boolean;
  pose_error: string | null;
  detector: string;
  /** Objects trained on this device, detected beside the stock ones. */
  trained?: string[];
  body: Body;
}

export interface CameraStatus {
  on: boolean;
  state: "off" | "starting" | "live" | "unavailable";
  index: number;
  detail: string | null;
  /** Selfie-style display; perception is never mirrored. */
  mirror: boolean;
}

export interface Alert {
  kind: string;
  message: string;
  severity: "low" | "medium" | "high" | string;
  seq: number;
}

export interface SessionStats {
  id: string;
  records: number;
  bytes: number;
  frames: number;
  video_bytes: number;
  rtsp: boolean;
  /** Why the requested RTSP stream is not running; null when it is, or none was asked for. */
  rtsp_error: string | null;
  /** A stopped recorder, or a recording a kill would lose; null when all is well. */
  record_issue: string | null;
  closed: boolean;
  elapsed_s: number;
  /** Head of the SHA-256 telemetry chain. */
  head: string;
}

/** On-device speech. When unavailable the browser speaks instead, and says why. */
export interface VoiceStatus {
  available: boolean;
  muted: boolean;
  reason: string | null;
  model: string;
  player: string | null;
  prewarmed: number;
  spoken: number;
}

export interface LiveState {
  phase: Phase;
  camera: CameraStatus;
  procedure: string;
  procedure_id: string;
  mode: string;
  open_vocab: boolean;
  fps: number;
  thresholds: { complete: number; abstain: number };
  perception: Perception;
  voice: VoiceStatus;
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

export interface LibraryEntry {
  id: string;
  title: string;
  summary: string;
  /** "builtin" ships with SARTHI; "saved" was built in the dashboard. */
  source: "builtin" | "saved";
  error?: string;
  name?: string;
  procedure_id?: string;
  steps?: string[];
  rack?: boolean;
  pose?: boolean;
  classes?: string[];
  /** The classes only training can provide: the stock detector knows the rest. */
  train?: string[];
  /** Detector class -> names of the steps that look for it. */
  uses?: Record<string, string[]>;
  missing?: string[];
  loaded?: boolean;
}

export interface LoadResult {
  ok: boolean;
  status: number;
  error?: string;
  missing?: string[];
  name?: string;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail ?? ""; } catch { /* not JSON */ }
    throw new Error(detail || `${r.status} ${r.statusText}`);
  }
  return r.json() as Promise<T>;
}

const post = (url: string) => fetch(url, { method: "POST" });

export const api = {
  // ---- run lifecycle
  /** Power the camera and begin a run. During a live run, this restarts it. */
  start: (mode: "clean" | "strict" = "clean") => post(`/api/session/start?mode=${mode}`),
  /** Seal the run and release the camera. */
  stop: () => post("/api/session/stop"),
  /** Camera without a run - the preview training capture needs. */
  camera: (on: boolean) => post(`/api/camera?on=${on}`),
  /** Selfie-style display. The picture only; coordinates never flip. */
  mirror: (on: boolean) => post(`/api/camera/mirror?on=${on}`),
  /** Body tracking: pose, hands, contact, gestures. */
  body: (on: boolean) => post(`/api/body?on=${on}`),
  skip: () => post("/api/skip"),
  /** Seal the run, release the camera, exit the process. */
  shutdown: () => post("/api/shutdown"),
  /** Mute on the device, where the voice actually is. */
  mute: (muted: boolean) => post(`/api/voice/mute?muted=${muted}`),

  // ---- procedures
  library: () => fetch("/api/procedures").then(j<LibraryEntry[]>),
  loadProcedure: async (id: string, start: boolean, force = false): Promise<LoadResult> => {
    const r = await fetch("/api/procedure/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, start, force }),
    });
    const body = await r.json().catch(() => ({}));
    return { ...body, ok: r.ok && !!body.ok, status: r.status };
  },
  classes: () => fetch("/api/classes").then(j<string[]>),
  /** Run builder steps once, without saving them. */
  runSteps: (steps: StepSpec[], name?: string) =>
    fetch("/api/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ steps, name, start: true }),
    }).then((r) => r.json() as Promise<{ ok: boolean; error?: string }>),

  // ---- saved experiments
  saveExperiment: (name: string, steps: StepSpec[], id?: string) =>
    fetch("/api/experiments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, steps, id }),
    }).then((r) => r.json() as Promise<{ ok: boolean; id?: string; error?: string }>),
  experiment: (id: string) =>
    fetch(`/api/experiments/${encodeURIComponent(id)}`)
      .then((r) => r.json() as Promise<{ ok: boolean; name?: string; steps?: StepSpec[]; error?: string }>),
  deleteExperiment: (id: string) =>
    fetch(`/api/experiments/${encodeURIComponent(id)}`, { method: "DELETE" })
      .then((r) => r.json() as Promise<{ ok: boolean; error?: string }>),

  // ---- training
  trainClasses: () =>
    fetch("/api/train/classes").then(
      j<{ classes: TrainClass[]; status: TrainStatus; model_ready: boolean;
          model_kind: TrainMode; advice: string[] }>
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
      .then((r) => r.json() as Promise<{ ok: boolean; saved?: number; error?: string }>),
  upload: (name: string, files: FileList) => {
    const fd = new FormData();
    fd.append("name", name);
    for (const f of Array.from(files)) fd.append("files", f);
    return fetch("/api/train/upload", { method: "POST", body: fd })
      .then((r) => r.json() as Promise<{ ok: boolean; saved?: number }>);
  },
  uploadVideo: (name: string, file: File, frames = 40) => {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("frames", String(frames));
    fd.append("file", file);
    return fetch("/api/train/video", { method: "POST", body: fd })
      .then((r) => r.json() as Promise<{ ok: boolean; saved?: number; error?: string }>);
  },
  startTrain: (epochs: number, mode: TrainMode) =>
    fetch(`/api/train/start?epochs=${epochs}&mode=${mode}`, { method: "POST" })
      .then((r) => r.json() as Promise<{ ok: boolean; message: string }>),
  trainStatus: () =>
    fetch("/api/train/status").then(j<{ status: TrainStatus; model_ready: boolean }>),
  /** Deploy the model and start a run - with a library procedure, or one step per class. */
  useModel: (procedure?: string) =>
    fetch(`/api/train/use${procedure ? `?procedure=${encodeURIComponent(procedure)}` : ""}`,
          { method: "POST" })
      .then((r) => r.json() as Promise<{ ok: boolean; error?: string; missing?: string[] }>),
  /** Create the classes a library procedure needs, plus background. */
  prepare: (procedure: string) =>
    fetch(`/api/train/prepare?procedure=${encodeURIComponent(procedure)}`, { method: "POST" })
      .then((r) => r.json() as Promise<{ ok: boolean; created?: string[]; error?: string }>),
  /** The trained model's verdict on the current frame, by the live-run rule. */
  predict: () => fetch("/api/train/predict").then((r) => r.json() as Promise<Prediction>),
  /** Let the stock detector find the object in every photo, for review. */
  proposeBoxes: () =>
    fetch("/api/train/boxes/propose", { method: "POST" }).then((r) => r.json() as Promise<BoxReview>),
  boxes: () => fetch("/api/train/boxes").then((r) => r.json() as Promise<BoxReview>),
  toggleBox: (cls: string, name: string) =>
    fetch(`/api/train/boxes/toggle?cls=${encodeURIComponent(cls)}&name=${encodeURIComponent(name)}`,
          { method: "POST" }).then((r) => r.json() as Promise<{ ok: boolean; excluded: boolean }>),
  boxImage: (cls: string, name: string) =>
    `/api/train/boxes/image?cls=${encodeURIComponent(cls)}&name=${encodeURIComponent(name)}`,

  // ---- archive
  sessions: () => fetch("/api/sessions?limit=200").then(j<SessionRow[]>),
  session: (id: string) => fetch(`/api/sessions/${id}`).then(j<SessionDetail>),
  verify: (id: string) =>
    fetch(`/api/sessions/${id}/verify`).then(
      j<{ ok: boolean; record_count: number; first_bad_seq: number | null; error: string | null }>
    ),
};

export interface TrainClass { name: string; count: number; background: boolean }

/** "detect" learns where the object is (boxes); "classify" learns whole scenes. */
export type TrainMode = "detect" | "classify";

/** One builder step: an object, a body action, or both, in the operator's words. */
export interface StepSpec {
  object?: string | null;
  gesture?: string | null;
  instruction?: string | null;
  /** Which hand must do it: "any", "left" or "right". */
  hand?: string;
  /** What is done with the object: "show", "hold", "pour" or "move". */
  how?: string;
}

export interface BoxReview {
  ok: boolean;
  classes: Record<string, { total: number; found: number; usable: number }>;
  flagged: string[];
  excluded: string[];
  /** Faint boxes from the closer look -- worth a glance before training. */
  weak?: string[];
  label: string | null;
  photos?: Record<string, string[]>;
}

export interface TrainStatus {
  state: "idle" | "training" | "done" | "error";
  message: string;
  epoch: number;
  epochs: number;
  model?: string;
  /** Held-out accuracy, per class. */
  report?: {
    accuracy: number | null;
    per_class: Record<string, { correct: number; total: number }>;
    taught_with?: Record<string, number>;
  };
  kind?: TrainMode;
}

export interface Prediction {
  ok: boolean;
  error?: string;
  name?: string;
  conf?: number;
  margin?: number;
  /** Would a live run count this frame? */
  accepted?: boolean;
  why?: string;
  classes?: { name: string; conf: number }[];
  kind?: TrainMode;
}

export interface SessionRow {
  id: string;
  procedure_id: string;
  status: "running" | "complete" | "aborted" | "crashed" | string;
  started_at: string;
  ended_at?: string | null;
  duration_ms: number | null;
  steps_total: number | null;
  steps_complete: number | null;
  steps_skipped: number | null;
  steps_out_of_order: number | null;
  alert_count: number | null;
  session_dir: string;
  /** What a recovered run lost, e.g. its video when the process was killed mid-recording. */
  notes?: string | null;
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
