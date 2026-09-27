import { useEffect, useRef, useState } from "react";
import type { LiveState } from "./api";

/** ~9 s of history at the server's 10 Hz push rate. */
const TRACE_LEN = 90;

export interface Traces {
  /** Confidence of the active step, reset whenever the active step changes. */
  confidence: number[];
  /** Perception loop rate. */
  fps: number[];
}

/** Subscribes to the engine's live state, and speaks only when the device cannot.
 *
 *  The device owns the voice: an alert that depends on somebody having a browser
 *  tab open is not a mission-critical alert. Browser speech stays as the fallback
 *  for when no voice model or audio player is installed - in which case the UI
 *  also says so rather than quietly sounding fine.
 */
export function useLive() {
  const [state, setState] = useState<LiveState | null>(null);
  const [connected, setConnected] = useState(false);
  const [traces, setTraces] = useState<Traces>({ confidence: [], fps: [] });
  const spokenStep = useRef<string | null>(null);
  const spokenAlert = useRef<number>(0);
  const traceKey = useRef<string | null>(null);
  const [muted, setMuted] = useState(false);
  const mutedRef = useRef(false);
  useEffect(() => { mutedRef.current = muted; }, [muted]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;

    const speak = (text: string) => {
      if (mutedRef.current || !text || !("speechSynthesis" in window)) return;
      try {
        const u = new SpeechSynthesisUtterance(text);
        u.rate = 1.0;
        window.speechSynthesis.speak(u);
      } catch { /* voice is advisory; never break the UI for it */ }
    };

    const record = (s: LiveState) => {
      const active = s.steps.find((x) => x.state === "active") ?? null;
      const key = active && s.phase === "live" ? `${s.session?.id}:${active.id}` : null;
      setTraces((prev) => {
        const confidence = key === traceKey.current && key
          ? [...prev.confidence, active!.confidence].slice(-TRACE_LEN)
          : key ? [active!.confidence] : [];
        traceKey.current = key;
        const fps = s.camera?.state === "live"
          ? [...prev.fps, s.fps].slice(-TRACE_LEN)
          : [];
        return { confidence, fps };
      });
    };

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, 1000);
      };
      ws.onmessage = (ev) => {
        let s: LiveState;
        try { s = JSON.parse(ev.data); } catch { return; }
        setState(s);
        record(s);

        // The device already spoke. Saying it twice is worse than not at all.
        if (s.voice?.available) {
          spokenStep.current = s.next?.id ?? null;
          spokenAlert.current = s.alert?.seq ?? 0;
          return;
        }
        // Nothing is announced between runs.
        if (s.phase !== "live") return;

        if (s.next && s.next.id !== spokenStep.current) {
          spokenStep.current = s.next.id;
          speak(s.next.voice);
        }
        if (!s.next) spokenStep.current = null;

        if (s.alert && s.alert.seq && s.alert.seq !== spokenAlert.current) {
          spokenAlert.current = s.alert.seq;
          speak(`Attention. ${s.alert.message}`);
        }
      };
    };
    connect();

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, []);

  /** Reset speech memory so a new run re-announces step one. */
  const resetSpeech = () => { spokenStep.current = null; spokenAlert.current = 0; };

  return { state, connected, muted, setMuted, resetSpeech, traces };
}

/** An alert stays up until acknowledged (UI brief §3); only low-severity
 *  advisories clear themselves. A newer alert always replaces an older one. */
export function useAlert(alert: LiveState["alert"] | undefined) {
  const [acked, setAcked] = useState(0);
  const [expired, setExpired] = useState(0);
  const seq = alert?.seq ?? 0;
  const low = alert?.severity === "low";

  useEffect(() => {
    if (!seq || !low) return;
    const t = window.setTimeout(() => setExpired(seq), 6000);
    return () => clearTimeout(t);
  }, [seq, low]);

  const visible = !!alert && seq > acked && !(low && expired === seq);
  return { visible, acknowledge: () => setAcked(seq) };
}
