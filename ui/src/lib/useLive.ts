import { useEffect, useRef, useState } from "react";
import type { LiveState } from "./api";

/** Subscribes to the engine's live state and speaks prompts and alerts.
 *
 *  Speech is done in the browser: it is offline (OS voices), needs no server
 *  audio device, and keeps the voice on the machine the operator is looking at.
 */
export function useLive() {
  const [state, setState] = useState<LiveState | null>(null);
  const [connected, setConnected] = useState(false);
  const spokenStep = useRef<string | null>(null);
  const spokenAlert = useRef<number>(0);
  const [muted, setMuted] = useState(false);
  const mutedRef = useRef(false);
  mutedRef.current = muted;

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

  return { state, connected, muted, setMuted, resetSpeech };
}

/** Shows an alert banner for a few seconds after each new alert. */
export function useAlertBanner(seq: number | undefined, message: string | undefined) {
  const [visible, setVisible] = useState(false);
  const last = useRef(0);
  useEffect(() => {
    if (!seq || seq === last.current) return;
    last.current = seq;
    setVisible(true);
    const t = setTimeout(() => setVisible(false), 5000);
    return () => clearTimeout(t);
  }, [seq, message]);
  return visible;
}
