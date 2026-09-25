import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { TrainStatus } from "../lib/api";
import { Empty, SectionTitle } from "../components/Bits";

type Cls = { name: string; count: number; background: boolean };

/** Teach the system a new object — or an object *state* (open vs closed).
 *  Classification needs no bounding boxes, so capturing pictures is enough. */
export default function Train({ resetSpeech }: { resetSpeech: () => void }) {
  const [classes, setClasses] = useState<Cls[]>([]);
  const [status, setStatus] = useState<TrainStatus | null>(null);
  const [ready, setReady] = useState(false);
  const [newName, setNewName] = useState("");
  const [epochs, setEpochs] = useState(20);
  const [busy, setBusy] = useState("");
  const poll = useRef<number | undefined>(undefined);

  const load = async () => {
    try {
      const d = await api.trainClasses();
      setClasses(d.classes); setStatus(d.status); setReady(d.model_ready);
    } catch { /* ignore */ }
  };
  useEffect(() => { load(); return () => { if (poll.current) clearInterval(poll.current); }; }, []);

  const hasBg = classes.some((c) => c.background && c.count > 0);
  const realClasses = classes.filter((c) => !c.background && c.count > 0);

  const shoot = async (name: string, n: number) => {
    for (let i = 1; i <= n; i++) {
      setBusy(n > 1 ? `📷 ${i}/${n} — keep moving "${name}"…` : `📷 captured for "${name}"`);
      await api.capture(name).catch(() => {});
      if (n > 1) await new Promise((r) => setTimeout(r, 330));
    }
    setBusy(`✓ ${n} picture${n > 1 ? "s" : ""} added to "${name}"`);
    load();
  };

  const start = async () => {
    const r = await api.startTrain(epochs);
    if (!r.ok) { setBusy(`✗ ${r.message}`); return; }
    setBusy("");
    if (poll.current) clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      const s = await api.trainStatus();
      setStatus(s.status); setReady(s.model_ready);
      if (s.status.state === "done" || s.status.state === "error") {
        clearInterval(poll.current); poll.current = undefined; load();
      }
    }, 1500);
  };

  const use = async () => {
    resetSpeech();
    const r = await api.useModel();
    setBusy(r.ok ? "✓ Model is live — open Mission" : `✗ ${r.error}`);
  };

  const training = status?.state === "training";
  const pct = training && status.epochs ? (status.epoch / status.epochs) * 100 : 0;

  return (
    <div className="page">
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        {/* classes */}
        <div className="panel panel-pad">
          <SectionTitle>Classes · one per object or state</SectionTitle>
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            <input className="input" placeholder="e.g. open_book" value={newName}
                   onChange={(e) => setNewName(e.target.value)}
                   onKeyDown={async (e) => {
                     if (e.key === "Enter" && newName.trim()) {
                       await api.addClass(newName.trim()); setNewName(""); load();
                     }
                   }} />
            <button className="btn btn-primary"
                    onClick={async () => { if (newName.trim()) { await api.addClass(newName.trim()); setNewName(""); load(); } }}>
              Add
            </button>
          </div>

          {!hasBg && (
            <div style={{
              background: "rgba(239,68,68,.1)", border: "1px solid var(--alert)",
              borderRadius: "var(--r-sm)", padding: 12, marginBottom: 14, fontSize: 12.5, lineHeight: 1.6,
            }}>
              <b style={{ color: "var(--alert)" }}>A “background” class is required.</b> A classifier
              always returns one of its classes — without images of the empty scene it will
              confidently guess a real object when nothing is there.
              <button className="btn" style={{ marginTop: 10 }}
                      onClick={async () => { await api.addClass("background"); load(); }}>
                ＋ Add background class
              </button>
            </div>
          )}

          {classes.length === 0 && <Empty>No classes yet.</Empty>}
          {classes.map((c) => (
            <div key={c.name} style={{
              display: "flex", alignItems: "center", gap: 8, padding: "9px 11px",
              background: "#0d141d", border: "1px solid var(--line)",
              borderRadius: "var(--r-sm)", marginBottom: 7,
            }}>
              <span style={{ flex: 1, fontSize: 13.5 }}>
                {c.background && <span title="negative class">🚫 </span>}{c.name}
              </span>
              <span className="mono" style={{ fontSize: 11, color: c.count < 10 ? "var(--caution)" : "var(--dim)" }}>
                {c.count} imgs
              </span>
              <button className="btn" style={{ padding: "4px 9px", fontSize: 11 }}
                      onClick={() => shoot(c.name, 1)}>📷 Snap</button>
              <button className="btn" style={{ padding: "4px 9px", fontSize: 11 }}
                      onClick={() => shoot(c.name, 10)}>×10</button>
              <label className="btn" style={{ padding: "4px 9px", fontSize: 11, cursor: "pointer" }}
                     title="upload images">
                ⭱
                <input type="file" accept="image/*" multiple style={{ display: "none" }}
                       onChange={async (e) => {
                         if (e.target.files?.length) { await api.upload(c.name, e.target.files); load(); }
                       }} />
              </label>
              <label className="btn" style={{ padding: "4px 9px", fontSize: 11, cursor: "pointer" }}
                     title="upload a video — frames become training images">
                🎬
                <input type="file" accept="video/*" style={{ display: "none" }}
                       onChange={async (e) => {
                         const f = e.target.files?.[0];
                         if (!f) return;
                         setBusy(`🎬 extracting frames from ${f.name}…`);
                         const r = await api.uploadVideo(c.name, f);
                         setBusy(r.ok ? `✓ ${r.saved} frames added to "${c.name}"` : `✗ ${r.error}`);
                         load();
                       }} />
              </label>
              <button className="btn btn-alert" style={{ padding: "4px 9px", fontSize: 11 }}
                      onClick={async () => { await api.delClass(c.name); load(); }}>✕</button>
            </div>
          ))}
        </div>

        {/* camera + training */}
        <div className="panel panel-pad">
          <SectionTitle>Camera · frame the object, then Snap</SectionTitle>
          <img src="/video" alt="preview"
               style={{ width: "100%", borderRadius: "var(--r-sm)", border: "1px solid var(--line)",
                        background: "#000", maxHeight: "32vh", objectFit: "contain" }} />

          <div style={{ fontSize: 12, color: "var(--dim)", margin: "12px 0", lineHeight: 1.65 }}>
            Capture from the camera rather than uploading photos — training images then match
            what the model sees at run time, which is the single biggest accuracy win.
            Aim for <b style={{ color: "var(--txt)" }}>20–30 per class</b>, moving the object between shots.
          </div>

          <div style={{ display: "flex", gap: 9, alignItems: "center", marginBottom: 12 }}>
            <span className="muted" style={{ fontSize: 12 }}>epochs</span>
            <input className="input" style={{ width: 80 }} value={epochs}
                   onChange={(e) => setEpochs(parseInt(e.target.value) || 20)} />
            <button className="btn btn-primary" style={{ flex: 1, justifyContent: "center" }}
                    disabled={training || realClasses.length < 1 || !hasBg} onClick={start}>
              {training ? "training…" : "⚙ Train model"}
            </button>
          </div>

          {training && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ height: 7, background: "#0d141d", borderRadius: 20, overflow: "hidden",
                            border: "1px solid var(--line)" }}>
                <div style={{ width: `${pct}%`, height: "100%", transition: "width .4s",
                              background: "linear-gradient(90deg,#0891b2,#22d3ee)" }} />
              </div>
              <div className="mono" style={{ fontSize: 11.5, color: "var(--accent)", marginTop: 6 }}>
                epoch {status.epoch}/{status.epochs}
              </div>
            </div>
          )}

          {status && !training && status.state !== "idle" && (
            <div className="mono" style={{
              fontSize: 12.5, marginBottom: 12,
              color: status.state === "done" ? "var(--accent)" : "var(--alert)",
            }}>
              {status.state === "done" ? "✓ " : "✗ "}{status.message}
            </div>
          )}

          {busy && <div className="mono" style={{ fontSize: 12, color: "var(--dim)", marginBottom: 12 }}>{busy}</div>}

          {ready && (
            <button className="btn btn-primary" style={{ width: "100%", justifyContent: "center" }} onClick={use}>
              ▶ Use this trained model live
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
