import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowDown, ArrowUp, BookOpen, Boxes, CircleCheck, Download, FolderHeart, Hand, Layers, Pencil,
  Play, Plus, Save, ScanLine, Search, Sparkles, Trash, TriangleAlert, X,
} from "lucide-react";
import { api } from "../lib/api";
import type { LibraryEntry, LiveState, LoadResult, StepSpec } from "../lib/api";
import { plural } from "../lib/format";
import { defaultWording, GESTURES, GROUP_LABEL, gestureLabel, HOWS, isOneHanded } from "../lib/gestures";
import type { GestureGroup } from "../lib/gestures";
import { Badge, Empty, ErrorNote, Note, PanelHead, Seg } from "../components/ui";

type Pending = { id: string; start: boolean; missing: string[] };
type Editing = { id: string; name: string; steps: StepSpec[] };

/** "PROC-A — Nested sample retrieval" → "PROC-A". */
function codeOf(e: LibraryEntry): string {
  const m = e.title.match(/^([A-Z0-9][A-Z0-9_-]*)\s+[—-]\s+/);
  return m ? m[1] : e.id.toUpperCase();
}

export default function Procedures({
  state, resetSpeech,
}: { state: LiveState | null; resetSpeech: () => void }) {
  const nav = useNavigate();
  const [lib, setLib] = useState<LibraryEntry[] | null>(null);
  const [libError, setLibError] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [flash, setFlash] = useState<{ tone: "ok" | "alert"; text: string } | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [armed, setArmed] = useState<string | null>(null);   // delete asks twice

  const refresh = useCallback(() => {
    api.library()
      .then((l) => { setLib(l); setLibError(""); })
      .catch((e) => setLibError(`Could not read the procedure library: ${e.message}`));
  }, []);
  useEffect(refresh, [refresh, state?.procedure_id]);

  const load = async (id: string, start: boolean, force = false) => {
    setFlash(null);
    const r: LoadResult = await api.loadProcedure(id, start, force).catch((e) => ({
      ok: false, status: 0, error: String(e.message ?? e),
    }));
    if (r.status === 409 && r.missing) { setPending({ id, start, missing: r.missing }); return; }
    setPending(null);
    if (!r.ok) { setFlash({ tone: "alert", text: r.error ?? "The server refused that procedure." }); return; }
    resetSpeech();
    if (start) { nav("/"); return; }
    setFlash({ tone: "ok", text: `Loaded “${r.name}”. Press New run on Mission when you are ready.` });
    refresh();
  };

  const edit = async (id: string) => {
    const r = await api.experiment(id);
    if (!r.ok || !r.steps) { setFlash({ tone: "alert", text: r.error ?? "Could not open that experiment." }); return; }
    setEditing({ id, name: r.name ?? id, steps: r.steps });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const remove = async (id: string) => {
    if (armed !== id) { setArmed(id); setTimeout(() => setArmed(null), 3000); return; }
    setArmed(null);
    const r = await api.deleteExperiment(id);
    setFlash(r.ok ? { tone: "ok", text: "Deleted." } : { tone: "alert", text: r.error ?? "Could not delete it." });
    if (editing?.id === id) setEditing(null);
    refresh();
  };

  const live = state?.phase === "live";
  const saved = (lib ?? []).filter((e) => e.source === "saved");
  const builtin = (lib ?? []).filter((e) => e.source !== "saved");
  const card = (e: LibraryEntry) => (
    <LibraryCard key={e.id} e={e} pending={pending?.id === e.id ? pending : null}
                 onRun={() => load(e.id, true)} onLoad={() => load(e.id, false)}
                 onForce={(start) => load(e.id, start, true)} onCancel={() => setPending(null)}
                 onEdit={e.source === "saved" ? () => edit(e.id) : undefined}
                 onDelete={e.source === "saved" ? () => remove(e.id) : undefined}
                 deleteArmed={armed === e.id} />
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Procedures</h1>
          <p className="page-desc">
            Build an experiment from objects and body actions, save it under a name, and run it
            again any time. Every experiment is a procedure file — the same kind SARTHI ships with.
          </p>
        </div>
        <span className="spacer" />
        {state && (
          <div className="card" style={{ padding: "10px 14px" }}>
            <div className="eyebrow">Loaded now</div>
            <div className="row" style={{ gap: 8, marginTop: 4 }}>
              <span style={{ fontWeight: 650 }}>{state.procedure}</span>
              <Badge tone={live ? "ok" : undefined}>{live ? "Running" : state.phase === "complete" ? "Complete" : "Ready"}</Badge>
            </div>
          </div>
        )}
      </div>

      {flash && (
        <Note tone={flash.tone} icon={flash.tone === "ok" ? CircleCheck : TriangleAlert}
              style={{ marginBottom: 16 }}>{flash.text}</Note>
      )}
      {live && (
        <Note tone="accent" style={{ marginBottom: 16 }}>
          A run is live. Running or loading another experiment seals it first — its record stays in the Archive.
        </Note>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 0.95fr) minmax(0, 1.15fr)", gap: 16, alignItems: "start" }}>
        <div className="col" style={{ gap: 16 }}>
          <div className="panel panel-pad">
            <PanelHead icon={FolderHeart} title="Your experiments"
                       right={<span className="mono faint" style={{ fontSize: 13 }}>{saved.length}</span>} />
            {libError && <ErrorNote message={libError} onRetry={refresh} />}
            {lib && saved.length === 0 && (
              <Empty icon={FolderHeart} title="Nothing saved yet">
                Build one on the right, give it a name and press Save.
              </Empty>
            )}
            <div className="list" style={{ gap: 10 }}>{saved.map(card)}</div>
          </div>

          <div className="panel panel-pad">
            <PanelHead icon={BookOpen} title="Built-in"
                       right={<span className="mono faint" style={{ fontSize: 13 }}>{builtin.length}</span>} />
            {!lib && !libError && <Empty title="Reading the library…" />}
            <div className="list" style={{ gap: 10 }}>{builtin.map(card)}</div>
          </div>
        </div>

        <Builder state={state} resetSpeech={resetSpeech} editing={editing}
                 onCancelEdit={() => setEditing(null)}
                 onSaved={(name, why) => {
                   setFlash(why
                     ? { tone: "alert", text: `Saved “${name}”, but it did not start: ${why}` }
                     : { tone: "ok", text: `Saved “${name}”. It is in Your experiments — run it any time, even after a restart.` });
                   setEditing(null);
                   refresh();
                 }} />
      </div>
    </div>
  );
}

function LibraryCard({
  e, pending, onRun, onLoad, onForce, onCancel, onEdit, onDelete, deleteArmed,
}: {
  e: LibraryEntry; pending: Pending | null;
  onRun: () => void; onLoad: () => void; onForce: (start: boolean) => void; onCancel: () => void;
  onEdit?: () => void; onDelete?: () => void; deleteArmed?: boolean;
}) {
  if (e.error) {
    return (
      <div className="card">
        <div className="row"><span className="mono" style={{ fontWeight: 650 }}>{e.id}.yaml</span><Badge tone="alert">Invalid</Badge></div>
        <div className="faint mono" style={{ fontSize: 13, marginTop: 8, wordBreak: "break-word" }}>{e.error}</div>
      </div>
    );
  }
  const missing = e.missing ?? [];
  const steps = e.steps ?? [];
  const isSaved = e.source === "saved";
  return (
    <div className={`card${e.loaded ? " loaded" : ""}`}>
      <div className="row" style={{ gap: 8 }}>
        <span className="eyebrow" style={{ color: "var(--accent)" }}>{isSaved ? "Saved" : codeOf(e)}</span>
        {e.loaded && <Badge tone="accent" icon={CircleCheck}>Loaded</Badge>}
        <span className="spacer" />
        {onEdit && <button className="btn btn-xs btn-ghost" onClick={onEdit}><Pencil size={13} /> Edit</button>}
        {onDelete && (
          <button className={`btn btn-xs ${deleteArmed ? "btn-danger" : "btn-ghost"}`} onClick={onDelete}
                  aria-label="Delete" title={deleteArmed ? "Click again to delete" : "Delete"}>
            <Trash size={13} /> {deleteArmed ? "Delete?" : ""}
          </button>
        )}
      </div>
      <div style={{ fontSize: 17, fontWeight: 650, letterSpacing: "-.01em", marginTop: 4 }}>{e.name}</div>
      {isSaved ? (
        <ol style={{ margin: "8px 0 0", paddingLeft: 22, color: "var(--ink-2)", fontSize: 14, lineHeight: 1.6 }}>
          {steps.slice(0, 5).map((s, i) => <li key={i}>{s}</li>)}
          {steps.length > 5 && <li className="faint" style={{ listStyle: "none" }}>+{steps.length - 5} more</li>}
        </ol>
      ) : e.summary && (
        <div className="faint" style={{ fontSize: 14, lineHeight: 1.55, marginTop: 6,
                                        display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {e.summary}
        </div>
      )}
      <div className="row wrap" style={{ gap: 6, marginTop: 10 }}>
        <Badge icon={Layers}>{plural(steps.length, "step")}</Badge>
        {(e.classes?.length ?? 0) > 0 && <Badge icon={Boxes}>{plural(e.classes!.length, "object")}</Badge>}
        {e.rack && <Badge icon={ScanLine}>Rack frame</Badge>}
        {e.pose && <Badge icon={Hand}>Body tracking</Badge>}
        {missing.length === 0 && <Badge tone="ok" icon={CircleCheck}>Detector ready</Badge>}
      </div>

      {missing.length > 0 && !pending && (
        <div className="faint" style={{ fontSize: 13.5, marginTop: 10, lineHeight: 1.5 }}>
          <TriangleAlert size={14} className="caution-ink" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          This detector cannot see {plural(missing.length, "class", "classes")}:{" "}
          <span className="mono muted">{missing.slice(0, 3).join(", ")}{missing.length > 3 ? ` +${missing.length - 3}` : ""}</span>
        </div>
      )}

      {pending ? (
        <Note tone="caution" style={{ marginTop: 12 }}>
          <div style={{ marginBottom: 8 }}>
            The loaded detector cannot perceive <b>{pending.missing.join(", ")}</b>. Steps that need them
            will never verify until a model that knows them is loaded.
          </div>
          <div className="row">
            <button className="btn btn-sm btn-caution" onClick={() => onForce(pending.start)}>
              {pending.start ? "Run anyway" : "Load anyway"}
            </button>
            <button className="btn btn-sm btn-ghost" onClick={onCancel}>Cancel</button>
          </div>
        </Note>
      ) : (
        <div className="row" style={{ marginTop: 12 }}>
          <span className="spacer" />
          <button className="btn btn-sm" onClick={onLoad} title="Load it; start from Mission">
            <Download size={14} /> Load
          </button>
          <button className="btn btn-sm btn-primary" onClick={onRun} title="Load it and start a run now">
            <Play size={13} fill="currentColor" /> Run
          </button>
        </div>
      )}
    </div>
  );
}

/* ================================================================ builder */

type Row = StepSpec & { key: number };
let nextKey = 1;
const row = (s: StepSpec): Row => ({
  key: nextKey++, object: s.object ?? null, gesture: s.gesture ?? null, instruction: s.instruction ?? null,
  hand: s.hand ?? "any", how: s.how ?? "show",
});
/** A hand can be asked for when a one-hand body action or a hand on the object is checked. */
const handApplies = (s: StepSpec): boolean => isOneHanded(s.gesture) || (!!s.object && (s.how ?? "show") !== "show");
/** Keep a step consistent after an edit: no "pour" without an object, no hand with nothing to check. */
const tidy = (r: Row): Row => {
  const how = r.object ? r.how ?? "show" : "show";
  const next = { ...r, how };
  return handApplies(next) ? next : { ...next, hand: "any" };
};

function Builder({
  state, resetSpeech, editing, onSaved, onCancelEdit,
}: {
  state: LiveState | null; resetSpeech: () => void; editing: Editing | null;
  onSaved: (name: string, why?: string) => void; onCancelEdit: () => void;
}) {
  const nav = useNavigate();
  const [classes, setClasses] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");
  const [palette, setPalette] = useState<"objects" | "body">("objects");
  const [q, setQ] = useState("");
  const [steps, setSteps] = useState<Row[]>([]);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const openVocab = !!state?.open_vocab;

  const load = useCallback(() => {
    api.classes()
      .then((c) => { setClasses(c); setLoadError(""); })
      .catch((e) => setLoadError(`Could not load the object list: ${e.message}`));
  }, []);
  useEffect(load, [load, state?.perception.detector]);

  // Opening an experiment for editing fills the builder with it.
  const [shown, setShown] = useState<string | null>(null);
  if ((editing?.id ?? null) !== shown) {
    setShown(editing?.id ?? null);
    if (editing) { setSteps(editing.steps.map(row)); setName(editing.name); setMsg(""); }
  }

  const add = (s: StepSpec) => setSteps((p) => (p.length >= 15 ? p : [...p, row(s)]));
  const patch = (key: number, s: Partial<StepSpec>) =>
    setSteps((p) => p.map((r) => (r.key === key ? tidy({ ...r, ...s }) : r)));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= steps.length) return;
    const n = [...steps];
    [n[i], n[j]] = [n[j], n[i]];
    setSteps(n);
  };

  const specs = (): StepSpec[] =>
    steps.map(({ object, gesture, instruction, hand, how }) =>
      ({ object, gesture, instruction: instruction?.trim() || null, hand: hand ?? "any", how: how ?? "show" }));
  const problem = (needName: boolean): string =>
    !steps.length ? "Add at least one step."
      : steps.some((s) => !s.object && !s.gesture) ? "Every step needs an object, a body action, or both."
      : needName && !name.trim() ? "Give the experiment a name to save it."
      : "";

  const save = async (andRun: boolean) => {
    const why = problem(true);
    if (why) { setMsg(why); return; }
    setBusy(true); setMsg("");
    const r = await api.saveExperiment(name.trim(), specs(), editing?.id).catch(() => ({ ok: false, error: "request failed", id: undefined }));
    setBusy(false);
    if (!r.ok || !r.id) { setMsg(r.error ?? "Could not save."); return; }
    let notStarted: string | undefined;
    if (andRun) {
      resetSpeech();
      // Not forced: an object this detector cannot see gets the same warning
      // as everywhere else, and "Run anyway" stays an explicit choice.
      const run = await api.loadProcedure(r.id, true, false);
      if (run.ok) { nav("/"); return; }
      notStarted = run.missing?.length
        ? `this detector cannot see ${run.missing.join(", ")}. Use Run anyway on its card if you mean it.`
        : run.error ?? "the server would not start it.";
    }
    onSaved(name.trim(), notStarted);
    setSteps([]); setName("");
  };

  const runOnce = async () => {
    const why = problem(false);
    if (why) { setMsg(why); return; }
    setBusy(true); setMsg("");
    const r = await api.runSteps(specs(), name.trim() || undefined).catch((e) => ({ ok: false, error: String(e) }));
    setBusy(false);
    if (!r.ok) { setMsg(r.error ?? "The server rejected those steps."); return; }
    resetSpeech();
    nav("/");
  };

  const needle = q.trim().toLowerCase();
  const shownClasses = classes.filter((c) => c.toLowerCase().includes(needle));
  // Objects trained on this device come first: they are what the operator added.
  const mine = new Set(state?.perception.trained ?? []);
  const shownMine = shownClasses.filter((c) => mine.has(c));
  const shownStock = shownClasses.filter((c) => !mine.has(c));
  const canType = openVocab && needle && !classes.some((c) => c.toLowerCase() === needle);
  const objectOptions = Array.from(new Set([...classes, ...steps.map((s) => s.object).filter(Boolean) as string[]]));

  return (
    <div className="panel panel-pad">
      <PanelHead icon={Sparkles}
                 title={editing ? <>Editing · {editing.name}</> : "Build an experiment"}
                 right={editing
                   ? <button className="btn btn-xs btn-ghost" onClick={() => { onCancelEdit(); setSteps([]); setName(""); }}><X size={13} /> Stop editing</button>
                   : <Badge>{openVocab ? "Any object you can name"
                       : `${classes.length} known objects${mine.size ? ` · ${mine.size} yours` : ""}`}</Badge>} />

      <div className="row" style={{ marginBottom: 12 }}>
        <input className="input" placeholder="Experiment name, e.g. Drink water" value={name}
               onChange={(e) => setName(e.target.value)} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 0.8fr) minmax(0, 1.2fr)", gap: 14 }}>
        {/* ---------------- palette */}
        <div>
          <Seg value={palette} onChange={setPalette} block
               options={[{ value: "objects", label: "Objects", icon: Boxes }, { value: "body", label: "Body actions", icon: Hand }]} />
          {palette === "objects" ? (
            <>
              <div className="input-wrap" style={{ margin: "10px 0" }}>
                <Search size={16} />
                <input className="input" placeholder={openVocab ? "Search, or type any object" : "Search objects"}
                       value={q} onChange={(e) => setQ(e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter" && canType) { add({ object: q.trim() }); setQ(""); } }} />
              </div>
              {loadError && <ErrorNote message={loadError} onRetry={load} />}
              <div className="tag-grid" style={{ maxHeight: "46vh", overflowY: "auto", paddingRight: 4 }}>
                {canType && (
                  <button className="tag" style={{ borderStyle: "dashed" }} onClick={() => { add({ object: q.trim() }); setQ(""); }}>
                    <Plus size={14} /> Add “{q.trim()}”
                  </button>
                )}
                {shownMine.length > 0 && (
                  <>
                    <div className="eyebrow" style={{ width: "100%" }}>Your trained objects</div>
                    {shownMine.map((c) => (
                      <button key={c} className="tag mine" onClick={() => add({ object: c })}><Plus size={14} />{c}</button>
                    ))}
                    <div className="eyebrow" style={{ width: "100%", marginTop: 6 }}>Stock objects</div>
                  </>
                )}
                {shownStock.map((c) => (
                  <button key={c} className="tag" onClick={() => add({ object: c })}><Plus size={14} />{c}</button>
                ))}
              </div>
            </>
          ) : (
            <div className="list" style={{ gap: 7, marginTop: 10, maxHeight: "52vh", overflowY: "auto", paddingRight: 4 }}>
              {(Object.keys(GROUP_LABEL) as GestureGroup[]).map((group) => (
                <div key={group} className="col" style={{ gap: 7 }}>
                  <div className="eyebrow" style={{ marginTop: group === "hand" ? 0 : 6 }}>{GROUP_LABEL[group]}</div>
                  {GESTURES.filter((g) => g.group === group).map(({ id, label, hint, icon: Icon }) => (
                    <button key={id} className="item" style={{ gridTemplateColumns: "34px minmax(0, 1fr) auto" }}
                            onClick={() => add({ gesture: id })}>
                      <span className="empty-icon" style={{ width: 34, height: 34 }}><Icon size={17} /></span>
                      <span style={{ minWidth: 0 }}>
                        <span style={{ display: "block", fontWeight: 650 }}>{label}</span>
                        <span className="faint" style={{ fontSize: 13 }}>{hint}</span>
                      </span>
                      <Plus size={16} className="faint" />
                    </button>
                  ))}
                </div>
              ))}
              <div className="faint" style={{ fontSize: 13, lineHeight: 1.55, marginTop: 4 }}>
                Read from the body's own posture, so they work however the person is turned. In a
                step, pick the hand (left or right) and what is done with the object — show, hold,
                pour or move. “Hand to face” holding the bottle is drinking.
              </div>
            </div>
          )}
        </div>

        {/* ---------------- sequence */}
        <div className="col" style={{ gap: 10 }}>
          <div className="list" style={{ gap: 7, maxHeight: "52vh", overflowY: "auto", minHeight: 140, paddingRight: 2 }}>
            {steps.length === 0 && <Empty icon={Layers}>Add objects or body actions.<br />Steps run top to bottom.</Empty>}
            {steps.map((s, i) => (
              <div key={s.key} className="seq-row fade-in" style={{ alignItems: "start" }}>
                <span className="seq-num" style={{ marginTop: 4 }}>{String(i + 1).padStart(2, "0")}</span>
                <div className="col" style={{ gap: 6, minWidth: 0 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                    <select className="input" style={{ height: 34, fontSize: 13.5 }} value={s.object ?? ""}
                            aria-label="Object"
                            onChange={(e) => patch(s.key, { object: e.target.value || null })}>
                      <option value="">No object</option>
                      {objectOptions.some((c) => mine.has(c)) ? (
                        <>
                          <optgroup label="Your trained objects">
                            {objectOptions.filter((c) => mine.has(c)).map((c) => <option key={c} value={c}>{c}</option>)}
                          </optgroup>
                          <optgroup label="Stock objects">
                            {objectOptions.filter((c) => !mine.has(c)).map((c) => <option key={c} value={c}>{c}</option>)}
                          </optgroup>
                        </>
                      ) : objectOptions.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <select className="input" style={{ height: 34, fontSize: 13.5 }} value={s.how ?? "show"}
                            aria-label="What is done with the object" disabled={!s.object}
                            onChange={(e) => patch(s.key, { how: e.target.value })}>
                      {HOWS.map((h) => <option key={h.id} value={h.id}>{h.label}</option>)}
                    </select>
                    <select className="input" style={{ height: 34, fontSize: 13.5 }} value={s.gesture ?? ""}
                            aria-label="Body action"
                            onChange={(e) => patch(s.key, { gesture: e.target.value || null })}>
                      <option value="">No body action</option>
                      {(Object.keys(GROUP_LABEL) as GestureGroup[]).map((group) => (
                        <optgroup key={group} label={GROUP_LABEL[group]}>
                          {GESTURES.filter((g) => g.group === group).map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}
                        </optgroup>
                      ))}
                    </select>
                    <select className="input" style={{ height: 34, fontSize: 13.5 }} value={s.hand ?? "any"}
                            aria-label="Which hand" disabled={!handApplies(s)}
                            title={handApplies(s) ? "Which hand must do it" : "Pick a one-hand action, or hold / pour / move an object"}
                            onChange={(e) => patch(s.key, { hand: e.target.value })}>
                      <option value="any">Either hand</option>
                      <option value="left">Left hand</option>
                      <option value="right">Right hand</option>
                    </select>
                  </div>
                  <input className="input" style={{ height: 34, fontSize: 14 }} value={s.instruction ?? ""}
                         placeholder={defaultWording(s.object, s.gesture, s.hand ?? "any", s.how ?? "show") || "What should the operator do?"}
                         onChange={(e) => patch(s.key, { instruction: e.target.value })} />
                </div>
                <span className="col" style={{ gap: 2 }}>
                  <button className="btn btn-xs btn-ghost btn-icon" onClick={() => move(i, -1)} disabled={i === 0} aria-label="Move up"><ArrowUp size={14} /></button>
                  <button className="btn btn-xs btn-ghost btn-icon" onClick={() => move(i, 1)} disabled={i === steps.length - 1} aria-label="Move down"><ArrowDown size={14} /></button>
                  <button className="btn btn-xs btn-ghost btn-icon" onClick={() => setSteps(steps.filter((r) => r.key !== s.key))} aria-label="Remove"><X size={14} /></button>
                </span>
              </div>
            ))}
          </div>
          {msg && <Note tone="alert">{msg}</Note>}
          <div className="row">
            <button className="btn" onClick={runOnce} disabled={busy || !steps.length} title="Run it now without saving">
              <Play size={13} fill="currentColor" /> Run once
            </button>
            <span className="spacer" />
            <button className="btn" onClick={() => save(false)} disabled={busy || !steps.length}>
              <Save size={15} /> {editing ? "Save changes" : "Save"}
            </button>
            <button className="btn btn-primary" onClick={() => save(true)} disabled={busy || !steps.length}>
              <Play size={13} fill="currentColor" /> Save & run
            </button>
          </div>
          <div className="faint" style={{ fontSize: 13, textAlign: "right" }}>
            {steps.length}/15 steps{steps.some((s) => s.gesture || (s.how ?? "show") !== "show") ? " · body tracking on for this run" : ""}
            {steps.some((s) => s.gesture) && <> · {Array.from(new Set(steps.filter((s) => s.gesture).map((s) => gestureLabel(s.gesture)))).join(", ")}</>}
          </div>
        </div>
      </div>
    </div>
  );
}
