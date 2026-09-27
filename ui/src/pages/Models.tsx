import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Aperture, BoxSelect, BrainCircuit, Camera, CameraOff, Check, CircleCheck, Film, FlaskConical,
  ImageUp, Layers, Lightbulb, Plus, Power, Rocket, ScanEye, ScanSearch, Tags, Trash, TriangleAlert,
  Zap,
} from "lucide-react";
import { api } from "../lib/api";
import type {
  BoxReview, LibraryEntry, LiveState, Prediction, TrainClass, TrainMode, TrainStatus,
} from "../lib/api";
import { plural } from "../lib/format";
import { detectorLabel } from "../lib/modes";
import { Stream } from "../components/Stream";
import { Badge, Empty, ErrorNote, Note, PanelHead, Seg } from "../components/ui";
import { Bar, Ring } from "../components/viz";

/** Recommended images per class. Three is the floor the trainer enforces;
 *  twenty is where a classifier starts generalising across angle and light. */
const TARGET = 20;
const MIN = 3;

const EPOCHS: Record<TrainMode, { value: string; label: string }[]> = {
  detect: [{ value: "10", label: "10" }, { value: "15", label: "15" }, { value: "25", label: "25" }],
  classify: [{ value: "10", label: "10" }, { value: "20", label: "20" }, { value: "40", label: "40" }],
};

const MODE_TEXT: Record<TrainMode, string> = {
  detect:
    "Learns where the object is. Your hand, arm and face fall outside its box, so they are "
    + "background automatically. The stock detector draws the boxes for you - it works for "
    + "everyday objects: books, bottles, cups, phones. Your objects join the 80 stock ones and "
    + "stay after a restart. About a minute per epoch on a laptop.",
  classify:
    "Learns what the whole picture looks like. Fast, and good for scenes (someone drinking), but "
    + "it learns anything that differs between your photo sets - where your arm comes from, "
    + "whether you sit in frame - so its background must be very varied.",
};

type Tone = "ok" | "alert" | "accent";

/** What a photo of this class has to show. The background advice is the one
 *  that matters most: it is the class that teaches the model what to ignore. */
function tipFor(cls: TrainClass): string {
  if (cls.background) {
    return "Everything that is NOT one of your classes: the empty spot, your empty hands, "
      + "a single finger, your face, other objects, you moving around. Give it more "
      + "photos than any other class.";
  }
  return "Show it the way the camera will see it during the run: different angles, "
    + "distances and light, either hand. If your hand is in these photos, background "
    + "needs photos of your empty hand too.";
}

/** Teach the system new objects - or object *states* (cap on, cap off) - by
 *  showing them to the camera. Classification needs no bounding boxes. */
export default function Models({
  state, resetSpeech,
}: { state: LiveState | null; resetSpeech: () => void }) {
  const nav = useNavigate();
  const [classes, setClasses] = useState<TrainClass[]>([]);
  const [advice, setAdvice] = useState<string[]>([]);
  const [status, setStatus] = useState<TrainStatus | null>(null);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [lib, setLib] = useState<LibraryEntry[]>([]);
  const [target, setTarget] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [trainMode, setTrainMode] = useState<TrainMode>("detect");
  const [epochs, setEpochs] = useState("15");
  const [modelKind, setModelKind] = useState<TrainMode>("classify");
  const [review, setReview] = useState<BoxReview | null>(null);
  const [proposing, setProposing] = useState(false);
  const [note, setNote] = useState<{ tone: Tone; text: string } | null>(null);
  const [armed, setArmed] = useState<string | null>(null);   // delete asks twice
  const [shooting, setShooting] = useState(false);
  const [flashKey, setFlashKey] = useState(0);
  const [mode, setMode] = useState<"capture" | "review" | "test">("capture");
  const [pred, setPred] = useState<Prediction | null>(null);
  const poll = useRef<number | undefined>(undefined);
  const weTurnedCameraOn = useRef(false);

  const load = useCallback(async () => {
    try {
      const d = await api.trainClasses();
      setClasses(d.classes); setStatus(d.status); setReady(d.model_ready);
      setAdvice(d.advice ?? []); setLoadError(""); setModelKind(d.model_kind ?? "classify");
      setSel((cur) => cur && d.classes.some((c) => c.name === cur) ? cur : d.classes[0]?.name ?? null);
      return d.status;
    } catch (e) {
      setLoadError(`Could not read the training set: ${(e as Error).message}`);
      return null;
    }
  }, []);

  const watch = useCallback(() => {
    if (poll.current) clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      try {
        const s = await api.trainStatus();
        setStatus(s.status); setReady(s.model_ready);
        if (s.status.state !== "training") { clearInterval(poll.current); poll.current = undefined; load(); }
      } catch { /* next tick retries */ }
    }, 1500);
  }, [load]);

  useEffect(() => {
    const first = window.setTimeout(() => {
      load().then((st) => { if (st?.state === "training") watch(); });
      api.library().then((l) => setLib(l.filter((e) => !e.error))).catch(() => { /* optional */ });
      api.boxes().then((b) => { if (b.ok) setReview(b); }).catch(() => { /* none yet */ });
    }, 0);
    return () => { clearTimeout(first); if (poll.current) clearInterval(poll.current); };
  }, [load, watch]);

  // A preview this page switched on is switched off when the page is left: an
  // idle console should not be quietly watching the room.
  const phaseRef = useRef(state?.phase);
  useEffect(() => { phaseRef.current = state?.phase; }, [state?.phase]);
  useEffect(() => () => {
    if (weTurnedCameraOn.current && phaseRef.current === "ready") api.camera(false);
  }, []);

  const camOn = !!state?.camera.on;
  const camLive = state?.camera.state === "live";
  const phase = state?.phase ?? "ready";

  // Test stage: ask the trained model about the current frame, ~3 times a
  // second, with exactly the rule a live run applies.
  useEffect(() => {
    if (mode !== "test" || !camLive || !ready) return;
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const p = await api.predict();
        if (alive) setPred(p);
      } catch { /* the next tick retries */ }
      if (alive) timer = window.setTimeout(tick, 330);
    };
    timer = window.setTimeout(tick, 0);
    return () => { alive = false; clearTimeout(timer); };
  }, [mode, camLive, ready]);

  // ---- stage status, mirroring TrainManager.can_train()
  const withImages = classes.filter((c) => c.count > 0);
  const hasBg = classes.some((c) => c.background);
  const bgImages = classes.some((c) => c.background && c.count > 0);
  const defined = classes.length >= 2 && hasBg;
  const captured = withImages.length >= 2 && withImages.every((c) => c.count >= MIN) && bgImages;
  // Suspect background photos still left out (the operator may bring some back).
  const suspects = review ? review.flagged.filter((k) => review.excluded.includes(k)).length : 0;
  const training = status?.state === "training";
  const deployed = state?.perception.detector === "classifier" || state?.perception.detector === "detector";
  const report = status?.report;
  const stages = [
    { title: "Classes", sub: defined ? `${plural(classes.length, "class", "classes")} · background ✓` : "Name objects & states", done: defined },
    { title: "Capture", sub: `${classes.reduce((a, c) => a + c.count, 0)} images`, done: captured },
    { title: "Train", sub: training ? `Epoch ${status!.epoch}/${status!.epochs}` : ready ? "Model trained" : "YOLO11n-cls", done: ready && !training },
    { title: "Test", sub: ready ? "Try it live, fix mistakes" : "After training", done: false },
    { title: "Deploy", sub: deployed ? "Live on the camera" : "Run a procedure", done: deployed },
  ];
  const current = ready && !training ? (deployed ? -1 : 3) : stages.findIndex((s) => !s.done);

  const targetEntry = lib.find((e) => e.id === target);
  const uses = targetEntry?.uses ?? {};
  // Only what training can provide: the stock detector already knows the rest.
  const missingForTarget = targetEntry
    ? (targetEntry.train ?? targetEntry.classes ?? []).filter((c) => !classes.some((k) => k.name === c))
    : [];

  // ---- actions
  const addClass = async (name: string) => {
    const n = name.trim();
    if (!n) return;
    await api.addClass(n);
    setNewName("");
    await load();
    setSel(n.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || n);
  };

  const prepare = async () => {
    if (!target) return;
    const r = await api.prepare(target);
    setNote(r.ok
      ? { tone: "ok", text: r.created?.length ? `Created ${r.created.join(", ")}.` : "Every class already exists." }
      : { tone: "alert", text: r.error ?? "Could not prepare the classes." });
    load();
  };

  const capture = async (n: number, into = sel) => {
    if (!into) return;
    setShooting(true); setNote(null);
    let saved = 0;
    for (let i = 0; i < n; i++) {
      const r = await api.capture(into).catch(() => ({ ok: false, error: "request failed" }));
      if (!r.ok) { setNote({ tone: "alert", text: r.error ?? "Capture failed." }); break; }
      saved++;
      setFlashKey((k) => k + 1);
      if (n > 1) {
        setNote({ tone: "accent", text: `Burst ${i + 1}/${n} - keep moving the object` });
        await new Promise((res) => setTimeout(res, 330));
      }
    }
    setShooting(false);
    if (saved) {
      setNote({ tone: "ok", text: mode === "test"
        ? `Filed this frame under “${into}”. Retrain when you have collected the mistakes.`
        : `${plural(saved, "image")} added to “${into}”.` });
    }
    load();
  };

  const upload = async (files: FileList | null) => {
    if (!sel || !files?.length) return;
    const r = await api.upload(sel, files).catch(() => null);
    setNote(r?.ok ? { tone: "ok", text: `${plural(r.saved ?? 0, "image")} added to “${sel}”.` }
                  : { tone: "alert", text: "Upload failed." });
    load();
  };

  const uploadVideo = async (file: File | undefined) => {
    if (!sel || !file) return;
    setNote({ tone: "accent", text: `Extracting frames from ${file.name}…` });
    const r = await api.uploadVideo(sel, file).catch(() => null);
    setNote(r?.ok ? { tone: "ok", text: `${plural(r.saved ?? 0, "frame")} added to “${sel}”.` }
                  : { tone: "alert", text: r?.error ?? "Could not read that video." });
    load();
  };

  const train = async () => {
    setNote(null);
    const r = await api.startTrain(Number(epochs), trainMode);
    if (!r.ok) { setNote({ tone: "alert", text: r.message }); return; }
    setStatus({ state: "training", message: "preparing data", epoch: 0, epochs: Number(epochs), kind: trainMode });
    watch();
  };

  const propose = async () => {
    setProposing(true); setNote(null);
    try {
      const r = await api.proposeBoxes();
      setReview(r);
      setMode("review");
    } catch {
      setNote({ tone: "alert", text: "Could not look for the object in your photos." });
    }
    setProposing(false);
  };

  const toggleBox = async (cls: string, name: string) => {
    const r = await api.toggleBox(cls, name);
    if (!r.ok) return;
    setReview((cur) => {
      if (!cur) return cur;
      const key = `${cls}/${name}`;
      const excluded = r.excluded ? [...cur.excluded, key] : cur.excluded.filter((k) => k !== key);
      return { ...cur, excluded };
    });
    // The counts come from the server, so what the page says is what training uses.
    api.boxes().then((b) => { if (b.ok) setReview(b); }).catch(() => { /* keep the optimistic view */ });
  };

  const pickMode = (m: TrainMode) => {
    setTrainMode(m);
    setEpochs(m === "detect" ? "15" : "20");
    if (m === "classify" && mode === "review") setMode("capture");
  };

  const deploy = async () => {
    resetSpeech();
    const r = await api.useModel(target || undefined);
    if (!r.ok) {
      setNote({ tone: "alert", text: r.missing?.length
        ? `The model was not trained on ${r.missing.join(", ")}. Create and capture those classes, then train again.`
        : r.error ?? "Could not load the model." });
      return;
    }
    nav("/");
  };

  const cameraOn = () => { weTurnedCameraOn.current = true; api.camera(true); };
  const selected = classes.find((c) => c.name === sel);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Models</h1>
          <p className="page-desc">
            Teach SARTHI objects and object <i>states</i> - cap on, cap off - by showing them to the
            camera. Everything runs on this device; no bounding boxes to draw.
          </p>
        </div>
        <span className="spacer" />
        <div className="card" style={{ padding: "10px 14px", minWidth: 280 }}>
          <div className="eyebrow">Training for</div>
          <select className="input" style={{ marginTop: 6, height: 36 }} value={target}
                  onChange={(e) => setTarget(e.target.value)}>
            <option value="">Any - one step per class</option>
            {lib.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
        </div>
        <div className="card" style={{ padding: "10px 14px" }}>
          <div className="eyebrow">Active detector</div>
          <div className="row" style={{ gap: 8, marginTop: 8 }}>
            <ScanEye size={17} className="accent-ink" />
            <span style={{ fontWeight: 650, whiteSpace: "nowrap" }}>{detectorLabel(state?.perception.detector, state?.perception.trained)}</span>
          </div>
        </div>
      </div>

      <div className="stepper">
        {stages.map((st, i) => (
          <div key={st.title} className={`stage${st.done ? " done" : i === current ? " current" : current >= 0 && i > current ? " locked" : ""}`}>
            <span className="stage-num">{st.done ? <Check size={17} strokeWidth={3} /> : i + 1}</span>
            <div style={{ minWidth: 0 }}>
              <div className="stage-title">{st.title}</div>
              <div className="stage-sub ellipsis">{st.sub}</div>
            </div>
          </div>
        ))}
      </div>

      {loadError && <ErrorNote message={loadError} onRetry={load} />}

      <div style={{ display: "grid", gridTemplateColumns: "360px minmax(0, 1fr) 360px", gap: 16, alignItems: "start" }}>
        {/* ------------------------------------------------------- classes */}
        <div className="panel panel-pad">
          <PanelHead icon={Tags} title="Classes" right={<span className="mono faint" style={{ fontSize: 13 }}>{classes.length}</span>} />

          {targetEntry && missingForTarget.length > 0 && (
            <Note tone="accent" icon={Layers} style={{ marginBottom: 12 }}>
              <b>{targetEntry.name}</b> needs {plural(missingForTarget.length, "class", "classes")} you
              do not have yet: <span className="mono">{missingForTarget.join(", ")}</span>.
              <div style={{ marginTop: 8 }}>
                <button className="btn btn-sm btn-primary" onClick={prepare}><Plus size={14} /> Create them</button>
              </div>
            </Note>
          )}

          <div className="row" style={{ marginBottom: 12 }}>
            <input className="input" placeholder="new class, e.g. cap_off" value={newName}
                   onChange={(e) => setNewName(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") addClass(newName); }} />
            <button className="btn btn-icon" style={{ width: 40, height: 40 }} onClick={() => addClass(newName)} aria-label="Add class">
              <Plus size={18} />
            </button>
          </div>

          {!hasBg && (
            <Note tone="caution" icon={TriangleAlert} style={{ marginBottom: 12 }}>
              <b>A background class is required.</b> The model always answers with one of its
              classes; background is how it says “none of these”.
              <div style={{ marginTop: 8 }}>
                <button className="btn btn-sm btn-caution" onClick={() => addClass("background")}>
                  <Plus size={14} /> Add background
                </button>
              </div>
            </Note>
          )}

          {classes.length === 0 && <Empty icon={Layers} title="No classes yet">One per object, or per state of an object.</Empty>}
          <div className="list" style={{ gap: 7 }}>
            {classes.map((c) => {
              const tone = c.count >= TARGET ? "var(--ok)" : c.count >= MIN ? "var(--accent)" : "var(--caution)";
              const usedBy = uses[c.name];
              return (
                <div key={c.name} className={`item clickable${sel === c.name ? " sel" : ""}`}
                     style={{ gridTemplateColumns: "minmax(0, 1fr) auto", cursor: "pointer" }}
                     onClick={() => setSel(c.name)}>
                  <div style={{ minWidth: 0 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <span className="ellipsis mono" style={{ fontSize: 14.5, fontWeight: 650 }}>{c.name}</span>
                      {c.background && <Badge>negative</Badge>}
                    </div>
                    {usedBy && (
                      <div className="faint ellipsis" style={{ fontSize: 13, marginTop: 3 }}>
                        {usedBy.join(" · ")}
                      </div>
                    )}
                    <div className="row" style={{ gap: 8, marginTop: 8 }}>
                      <div style={{ flex: 1 }}><Bar value={c.count / TARGET} color={tone} /></div>
                      <span className="mono" style={{ fontSize: 13, color: tone, minWidth: 46, textAlign: "right" }}>
                        {c.count}/{TARGET}
                      </span>
                    </div>
                  </div>
                  <button className={`btn btn-xs btn-icon ${armed === c.name ? "btn-danger" : "btn-ghost"}`}
                          style={armed === c.name ? { width: "auto", padding: "0 8px" } : undefined}
                          title="Delete this class and its images"
                          onClick={async (e) => {
                            e.stopPropagation();
                            if (armed !== c.name) { setArmed(c.name); setTimeout(() => setArmed(null), 3000); return; }
                            await api.delClass(c.name); setArmed(null); load();
                          }}>
                    <Trash size={14} />{armed === c.name && "Delete?"}
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {/* ------------------------------------------------ capture / test */}
        <div className="panel panel-pad">
          <PanelHead icon={Aperture}
                     title={mode === "test" ? "Test the model" : mode === "review" ? "Review boxes" : <>Capture{selected && <span className="faint" style={{ fontWeight: 600 }}> · {selected.name}</span>}</>}
                     right={<Seg value={mode} onChange={(m) => { setMode(m); setPred(null); setNote(null); }}
                                 options={[{ value: "capture", label: "Capture", icon: Camera },
                                           ...(trainMode === "detect" ? [{ value: "review" as const, label: "Boxes", icon: BoxSelect }] : []),
                                           { value: "test", label: "Test model", icon: FlaskConical }]} />} />

          {mode === "review" ? (
            <BoxGallery review={review} onToggle={toggleBox} onPropose={propose} proposing={proposing} />
          ) : (
          <div className="preview">
            {/* A shutter flash per capture. Keyed on its own element: re-keying
                the <img> would tear down and reopen the camera stream. */}
            {flashKey > 0 && <span key={flashKey} className="shutter" />}
            {camOn ? (
              <>
                <Stream alt="Camera preview" />
                <div className="preview-tags">
                  <span className="hud-tag">{phase === "live" ? "Shared with live run" : camLive ? "Camera on" : "Starting…"}</span>
                  <span className="spacer" />
                  <button className={`hud-tag${state?.camera.mirror ? " on" : ""}`}
                          onClick={() => api.mirror(!state?.camera.mirror)}>
                    Mirror {state?.camera.mirror ? "on" : "off"}
                  </button>
                </div>
                {mode === "test" && pred?.ok && (
                  <div className={`verdict-strip ${pred.accepted ? "yes" : "no"}`}>
                    {pred.accepted ? <CircleCheck size={22} color="#5cc68e" /> : <TriangleAlert size={22} color="#9a958a" />}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {pred.kind === "detect" && !pred.accepted
                        ? <b>Nothing found</b>
                        : <><b>{pred.name}</b> <span style={{ opacity: .8 }}>{pred.conf?.toFixed(2)}</span></>}
                      <div style={{ fontSize: 14, opacity: .85 }}>
                        {pred.accepted ? "A run would count this - the step would move forward." : `A run would ignore this: ${pred.why}.`}
                      </div>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="overlay standby" style={{ padding: 20 }}>
                <div className="standby-body">
                  <div className="empty-icon" style={{ width: 54, height: 54 }}><CameraOff size={24} /></div>
                  <div className="standby-text">The camera is off. Turn it on to frame the object - nothing is judged or logged.</div>
                  <button className="btn btn-primary" onClick={cameraOn}><Power size={16} /> Turn camera on</button>
                </div>
              </div>
            )}
          </div>
          )}

          {mode === "review" ? null : mode === "capture" ? (
            <>
              <div className="row wrap" style={{ marginTop: 12 }}>
                <button className="btn btn-primary" disabled={!sel || !camLive || shooting} onClick={() => capture(1)}>
                  <Camera size={17} /> Capture
                </button>
                <button className="btn" disabled={!sel || !camLive || shooting} onClick={() => capture(10)}>
                  <Zap size={16} /> Burst ×10
                </button>
                <span className="spacer" />
                <label className={`btn btn-sm${sel ? "" : " disabled"}`} title="Add photos from disk">
                  <ImageUp size={15} /> Images
                  <input type="file" accept="image/*" multiple hidden disabled={!sel}
                         onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
                </label>
                <label className={`btn btn-sm${sel ? "" : " disabled"}`} title="Frames from a short clip become training images">
                  <Film size={15} /> Video
                  <input type="file" accept="video/*" hidden disabled={!sel}
                         onChange={(e) => { uploadVideo(e.target.files?.[0]); e.target.value = ""; }} />
                </label>
                {camOn && phase === "ready" && (
                  <button className="btn btn-sm btn-ghost" onClick={() => { weTurnedCameraOn.current = false; api.camera(false); }}>
                    <CameraOff size={15} /> Off
                  </button>
                )}
              </div>
              {selected && (
                <Note tone={selected.background ? "caution" : undefined} icon={Lightbulb} style={{ marginTop: 12 }}>
                  <b className="mono">{selected.name}</b> - {tipFor(selected)}
                </Note>
              )}
            </>
          ) : !ready ? (
            <Empty icon={FlaskConical} title="Train a model first">Then test it here, live, before trusting it with a run.</Empty>
          ) : !camOn ? (
            <Empty icon={CameraOff}>Turn the camera on to test.</Empty>
          ) : (
            <>
              <div className="probs">
                {pred?.ok && pred.kind === "detect" && (pred.classes ?? []).length === 0 && (
                  <div className="faint" style={{ fontSize: 14 }}>No detections in this frame.</div>
                )}
                {(pred?.classes ?? []).map((c, i) => (
                  <div key={c.name} className={`prob${i === 0 ? " top" : ""}`}>
                    <span className="ellipsis">{c.name}</span>
                    <div className="bar"><i style={{ width: `${Math.round(c.conf * 100)}%` }} /></div>
                    <span style={{ textAlign: "right" }}>{c.conf.toFixed(2)}</span>
                  </div>
                ))}
                {pred && !pred.ok && <Note tone="alert">{pred.error}</Note>}
              </div>
              <div className="divider" />
              <div className="eyebrow" style={{ marginBottom: 8 }}>Wrong? This frame is really…</div>
              <div className="row wrap" style={{ gap: 8 }}>
                {classes.map((c) => (
                  <button key={c.name} className={`btn btn-sm${c.background ? " btn-caution" : ""}`}
                          disabled={shooting || !camLive} onClick={() => capture(1, c.name)}>
                    <Plus size={14} /> {c.name}
                  </button>
                ))}
              </div>
              <div className="faint" style={{ fontSize: 13.5, marginTop: 10, lineHeight: 1.55 }}>
                Show the model what it gets wrong - your empty hand, a finger, your face - and file each
                frame under the right class. Then train again. This is how a model learns what to ignore.
              </div>
            </>
          )}

          {note && <Note tone={note.tone} icon={note.tone === "ok" ? CircleCheck : note.tone === "alert" ? TriangleAlert : Camera}
                         style={{ marginTop: 12 }}>{note.text}</Note>}
        </div>

        {/* ------------------------------------------------- train + deploy */}
        <div className="col" style={{ gap: 16 }}>
          <div className="panel panel-pad">
            <PanelHead icon={BrainCircuit} title="Train" />
            <Seg value={trainMode} onChange={pickMode} block
                 options={[{ value: "detect", label: "Objects", icon: ScanSearch },
                           { value: "classify", label: "Whole scene", icon: Aperture }]} />
            <div className="faint" style={{ fontSize: 13.5, lineHeight: 1.55, margin: "10px 0 12px" }}>
              {MODE_TEXT[trainMode]}
            </div>
            {trainMode === "detect" && (
              <div className="card" style={{ padding: 12, marginBottom: 12 }}>
                {review && Object.keys(review.classes).length > 0 ? (
                  <>
                    {Object.entries(review.classes).map(([cls, v]) => (
                      <div key={cls} className="kv">
                        <span className="mono">{cls}</span>
                        <span className="mono" style={{ color: v.usable / Math.max(v.total, 1) >= 0.7 ? "var(--ok)" : "var(--caution)" }}>
                          {v.usable}/{v.total} boxed
                        </span>
                      </div>
                    ))}
                    {suspects > 0 && (
                      <Note tone="caution" style={{ marginTop: 8 }}>
                        {suspects} background photo{suspects > 1 ? "s seem" : " seems"} to
                        show a {review.label}. {suspects > 1 ? "They are" : "It is"} left out - check in Boxes.
                      </Note>
                    )}
                    <div className="row" style={{ marginTop: 8 }}>
                      <button className="btn btn-sm" onClick={() => setMode("review")}><BoxSelect size={14} /> Review</button>
                      <span className="spacer" />
                      <button className="btn btn-sm btn-ghost" onClick={propose} disabled={proposing}>
                        {proposing ? "Looking…" : "Look again"}
                      </button>
                    </div>
                  </>
                ) : (
                  <button className="btn btn-block" onClick={propose} disabled={proposing || !captured}>
                    <ScanSearch size={16} /> {proposing ? "Looking for the object…" : "Find the object in my photos"}
                  </button>
                )}
              </div>
            )}
            <div style={{ marginBottom: 12 }}>
              <Check2 ok={withImages.length >= 2} text="At least two classes with images" />
              <Check2 ok={bgImages} text="Background class has images" />
              <Check2 ok={withImages.length > 0 && withImages.every((c) => c.count >= MIN)}
                      text={`Every class has ${MIN}+ images (${TARGET}+ recommended)`} />
            </div>

            {advice.length > 0 && (
              <div className="col" style={{ gap: 8, marginBottom: 12 }}>
                {advice.map((a) => <Note key={a} tone="caution" icon={Lightbulb}>{a}</Note>)}
              </div>
            )}

            {training ? (
              <div className="row" style={{ gap: 16, padding: "6px 0 4px" }}>
                <Ring value={status!.epochs ? status!.epoch / status!.epochs : 0} size={80} stroke={6}>
                  <div className="mono" style={{ fontSize: 16, fontWeight: 650 }}>
                    {status!.epoch}<span className="faint">/{status!.epochs}</span>
                  </div>
                </Ring>
                <div>
                  <div style={{ fontWeight: 650 }}>Training on this device</div>
                  <div className="faint" style={{ fontSize: 14 }}>{status!.message || "working"} · you can keep using SARTHI</div>
                </div>
              </div>
            ) : (
              <>
                <div className="row" style={{ marginBottom: 6 }}>
                  <span className="eyebrow">Epochs</span>
                  <span className="spacer" />
                  <span className="faint" style={{ fontSize: 13 }}>more is slower</span>
                </div>
                <Seg value={epochs} onChange={setEpochs} options={EPOCHS[trainMode]} block />
                <button className="btn btn-primary btn-block" style={{ marginTop: 12 }} disabled={!captured} onClick={train}>
                  <BrainCircuit size={17} /> {trainMode === "detect" ? "Train object detector" : "Train scene classifier"}
                </button>
              </>
            )}

            {report && !training && (
              <div style={{ marginTop: 14 }}>
                <div className="row" style={{ marginBottom: 6 }}>
                  <span className="eyebrow">Held-out accuracy</span>
                  <span className="spacer" />
                  <span className="mono" style={{ fontWeight: 700 }}>
                    {report.accuracy == null ? "-" : `${Math.round(report.accuracy * 100)}%`}
                  </span>
                </div>
                {Object.entries(report.per_class).map(([name, v]) => (
                  <div key={name} className="kv">
                    <span className="mono">{name}</span>
                    <span className="mono" style={{ color: v.correct === v.total ? "var(--ok)" : "var(--caution)" }}>
                      {v.correct}/{v.total}
                    </span>
                  </div>
                ))}
                {report.taught_with && (
                  <div className="faint mono" style={{ fontSize: 12.5, marginTop: 6 }}>
                    taught with {Object.entries(report.taught_with).map(([k, v]) => `${v} ${k}`).join(" · ")}
                  </div>
                )}
                <div className="faint" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
                  {modelKind === "detect"
                    ? "Background is right only when nothing at all is found. Test it live before trusting it."
                    : "These photos come from your own capture sessions, so a high score is necessary, not sufficient. Test it live before trusting it."}
                </div>
              </div>
            )}

            {status?.state === "error" && (
              <Note tone="alert" style={{ marginTop: 12 }}>Training failed: {status.message}</Note>
            )}
          </div>

          <div className={`panel panel-pad${ready && !deployed ? " ready-glow" : ""}`}>
            <PanelHead icon={Rocket} title="Deploy" right={deployed ? <Badge tone="ok">Live</Badge> : ready ? <Badge tone="accent">{modelKind === "detect" ? "Detector ready" : "Classifier ready"}</Badge> : null} />
            {ready ? (
              <>
                <div className="faint" style={{ fontSize: 14, lineHeight: 1.55, marginBottom: 12 }}>
                  {targetEntry
                    ? <>{modelKind === "detect" ? "Add your objects beside the 80 stock ones" : "Swap the detector for your classifier"} and
                        start <b style={{ color: "var(--ink)" }}>{targetEntry.name}</b>.</>
                    : modelKind === "detect"
                      ? "Add your objects beside the 80 stock ones and start a run where each of yours is one step."
                      : "Swap the detector for your classifier and start a run where each class is one step."}
                </div>
                <button className="btn btn-primary btn-block" onClick={deploy}><Rocket size={17} /> Deploy & run</button>
              </>
            ) : (
              <div className="faint" style={{ fontSize: 14, lineHeight: 1.55 }}>Train a model first.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Check2({ ok, text }: { ok: boolean; text: string }) {
  return (
    <div className={`check ${ok ? "ok" : "no"}`}>
      {ok ? <CircleCheck size={17} /> : <span style={{ width: 17, height: 17, borderRadius: 4, border: "1.5px solid var(--ink-4)", flex: "none" }} />}
      <span style={ok ? undefined : { color: "var(--ink-3)" }}>{text}</span>
    </div>
  );
}

/** Every photo with the box the stock detector proposed. Click to leave one
 *  out of training -- a wrong box teaches the wrong thing. */
function BoxGallery({
  review, onToggle, onPropose, proposing,
}: {
  review: BoxReview | null;
  onToggle: (cls: string, name: string) => void;
  onPropose: () => void;
  proposing: boolean;
}) {
  if (!review || !review.photos || Object.keys(review.classes).length === 0) {
    return (
      <Empty icon={ScanSearch} title="No boxes yet">
        Capture your photos, then let SARTHI find the object in them.
        <div style={{ marginTop: 10 }}>
          <button className="btn btn-primary" onClick={onPropose} disabled={proposing}>
            <ScanSearch size={16} /> {proposing ? "Looking…" : "Find the object in my photos"}
          </button>
        </div>
      </Empty>
    );
  }
  const excluded = new Set(review.excluded);
  const weak = new Set(review.weak ?? []);
  const flagged = new Set(review.flagged);
  return (
    <div style={{ maxHeight: "62vh", overflowY: "auto", paddingRight: 4 }}>
      <div className="faint" style={{ fontSize: 13.5, marginBottom: 10, lineHeight: 1.5 }}>
        The saffron box is what the detector will learn as your object. Click a photo whose box is
        wrong to leave it out. Photos with no box are not used; boxes marked <b>faint</b> came from a
        closer look and deserve a glance.
      </div>
      {Object.entries(review.photos).map(([cls, names]) => (
        <div key={cls} style={{ marginBottom: 14 }}>
          <div className="row" style={{ marginBottom: 6 }}>
            <span className="eyebrow">{cls}</span>
            <span className="spacer" />
            <span className="mono faint" style={{ fontSize: 12.5 }}>
              {review.classes[cls]
                ? `${review.classes[cls].usable}/${review.classes[cls].total} used`
                : `${names.filter((n) => !excluded.has(`${cls}/${n}`)).length}/${names.length} suspect photos used`}
            </span>
          </div>
          {!review.classes[cls] && (
            <div className="faint" style={{ fontSize: 13, marginBottom: 6, lineHeight: 1.5 }}>
              These seem to show a {review.label ?? "target"}, so they are left out: a negative that
              contains the object teaches “object = nothing”. Click one the detector got wrong to use it.
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(118px, 1fr))", gap: 6 }}>
            {names.map((name) => {
              const off = excluded.has(`${cls}/${name}`);
              return (
                <button key={name} onClick={() => onToggle(cls, name)} title={off ? "Left out - click to use" : "Used - click to leave out"}
                        style={{ position: "relative", padding: 0, border: `2px solid ${off ? "var(--alert-line)" : "transparent"}`,
                                 borderRadius: 6, overflow: "hidden", cursor: "pointer", background: "var(--well)", opacity: off ? 0.45 : 1 }}>
                  <img src={api.boxImage(cls, name)} alt={name} loading="lazy" style={{ display: "block", width: "100%" }} />
                  {off && <span className="badge alert" style={{ position: "absolute", left: 4, top: 4 }}>left out</span>}
                  {!off && flagged.has(`${cls}/${name}`) && (
                    <span className="badge caution" style={{ position: "absolute", left: 4, top: 4 }}>used anyway</span>
                  )}
                  {!off && weak.has(`${cls}/${name}`) && (
                    <span className="badge caution" style={{ position: "absolute", left: 4, top: 4 }}>faint</span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
