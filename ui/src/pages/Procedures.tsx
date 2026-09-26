import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowDown, ArrowUp, BookOpen, Boxes, CircleCheck, Download, Hand, Layers, Play, Plus,
  ScanLine, Search, Sparkles, TriangleAlert, X,
} from "lucide-react";
import { api } from "../lib/api";
import type { LibraryEntry, LiveState, LoadResult } from "../lib/api";
import { plural } from "../lib/format";
import { Badge, Empty, ErrorNote, Note, PanelHead } from "../components/ui";

type Pending = { id: string; start: boolean; missing: string[] };

/** "PROC-A — Nested sample retrieval" → "PROC-A". */
function codeOf(e: LibraryEntry): string {
  const m = e.title.match(/^([A-Z0-9][A-Z0-9-]*)\s+[—-]\s+/);
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
    setFlash({ tone: "ok", text: `Loaded “${r.name}”. Press New run on Mission when the crew is ready.` });
    refresh();
  };

  const live = state?.phase === "live";

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Procedures</h1>
          <p className="page-desc">
            Procedures are configuration, not code — each one is a YAML file. Run a flight procedure
            from the library, or compose a quick sequence from objects the detector already knows.
          </p>
        </div>
        <span className="spacer" />
        {state && (
          <div className="card" style={{ padding: "10px 14px" }}>
            <div className="eyebrow">Loaded now</div>
            <div className="row" style={{ gap: 8, marginTop: 2 }}>
              <span style={{ fontWeight: 600 }}>{state.procedure}</span>
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
          A run is live. Loading another procedure seals it first — its record stays in the Archive.
        </Note>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.05fr)", gap: 16, alignItems: "start" }}>
        {/* ------------------------------------------------------- library */}
        <div className="panel panel-pad">
          <PanelHead icon={BookOpen} title="Library"
                     right={lib && <span className="mono faint" style={{ fontSize: 13 }}>{plural(lib.length, "file")}</span>} />
          {libError && <ErrorNote message={libError} onRetry={refresh} />}
          {!lib && !libError && <Empty title="Reading the library…" />}
          {lib && lib.length === 0 && <Empty icon={BookOpen} title="No procedures found">Add a YAML file to the procedures folder.</Empty>}
          <div className="list" style={{ gap: 10 }}>
            {lib?.map((e) => (
              <LibraryCard key={e.id} e={e} pending={pending?.id === e.id ? pending : null}
                           onRun={() => load(e.id, true)} onLoad={() => load(e.id, false)}
                           onForce={(start) => load(e.id, start, true)} onCancel={() => setPending(null)} />
            ))}
          </div>
        </div>

        {/* ------------------------------------------------------- builder */}
        <Builder state={state} resetSpeech={resetSpeech} />
      </div>
    </div>
  );
}

function LibraryCard({
  e, pending, onRun, onLoad, onForce, onCancel,
}: {
  e: LibraryEntry; pending: Pending | null;
  onRun: () => void; onLoad: () => void; onForce: (start: boolean) => void; onCancel: () => void;
}) {
  if (e.error) {
    return (
      <div className="card">
        <div className="row"><span className="mono" style={{ fontWeight: 600 }}>{e.id}.yaml</span><Badge tone="alert">Invalid</Badge></div>
        <div className="faint mono" style={{ fontSize: 13, marginTop: 8, wordBreak: "break-word" }}>{e.error}</div>
      </div>
    );
  }
  const missing = e.missing ?? [];
  return (
    <div className={`card${e.loaded ? " loaded" : ""}`}>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="row" style={{ gap: 8 }}>
            <span className="eyebrow" style={{ color: "var(--accent-2)" }}>{codeOf(e)}</span>
            {e.loaded && <Badge tone="accent" icon={CircleCheck}>Loaded</Badge>}
          </div>
          <div style={{ fontSize: 17, fontWeight: 620, letterSpacing: "-.01em", marginTop: 3 }}>{e.name}</div>
        </div>
      </div>
      {e.summary && (
        <div className="faint" style={{ fontSize: 14, lineHeight: 1.55, marginTop: 6,
                                        display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
          {e.summary}
        </div>
      )}
      <div className="row wrap" style={{ gap: 6, marginTop: 10 }}>
        <Badge icon={Layers}>{plural(e.steps?.length ?? 0, "step")}</Badge>
        <Badge icon={Boxes}>{plural(e.classes?.length ?? 0, "class", "classes")}</Badge>
        {e.rack && <Badge icon={ScanLine}>Rack frame</Badge>}
        {e.pose && <Badge icon={Hand}>Pose + contact</Badge>}
        {missing.length === 0 && <Badge tone="ok" icon={CircleCheck}>Detector ready</Badge>}
      </div>

      {missing.length > 0 && !pending && (
        <div className="faint" style={{ fontSize: 13.5, marginTop: 10, lineHeight: 1.5 }}>
          <TriangleAlert size={13} className="caution-ink" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          This detector cannot see {plural(missing.length, "class", "classes")}:{" "}
          <span className="mono muted">{missing.slice(0, 3).join(", ")}{missing.length > 3 ? ` +${missing.length - 3}` : ""}</span>
        </div>
      )}

      {pending ? (
        <Note tone="caution" style={{ marginTop: 12 }}>
          <div style={{ marginBottom: 8 }}>
            The loaded detector cannot perceive <b>{pending.missing.join(", ")}</b>. Steps that need them
            will never verify until the trained model is loaded.
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
            <Download size={13} /> Load
          </button>
          <button className="btn btn-sm btn-primary" onClick={onRun} title="Load it and start a run now">
            <Play size={12} fill="currentColor" /> Run
          </button>
        </div>
      )}
    </div>
  );
}

/* ================================================================ builder */

function Builder({ state, resetSpeech }: { state: LiveState | null; resetSpeech: () => void }) {
  const nav = useNavigate();
  const [classes, setClasses] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");
  const [q, setQ] = useState("");
  const [seq, setSeq] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState("");
  const openVocab = !!state?.open_vocab;

  const load = useCallback(() => {
    api.classes()
      .then((c) => { setClasses(c); setLoadError(""); })
      .catch((e) => setLoadError(`Could not load the object list: ${e.message}`));
  }, []);
  useEffect(load, [load, state?.perception.detector]);

  const add = (c: string) => { if (c.trim() && seq.length < 15) setSeq((p) => [...p, c.trim()]); };
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= seq.length) return;
    const n = [...seq];
    [n[i], n[j]] = [n[j], n[i]];
    setSeq(n);
  };

  const launch = async () => {
    setMsg("");
    if (!seq.length) { setMsg("Add at least one object first."); return; }
    try {
      const r = await api.build(seq, name.trim() || undefined);
      if (!r.ok) { setMsg(r.error ?? "The server rejected that sequence."); return; }
      resetSpeech();
      nav("/");
    } catch (e) {
      setMsg(`Could not start: ${(e as Error).message}`);
    }
  };

  const needle = q.trim().toLowerCase();
  const shown = classes.filter((c) => c.toLowerCase().includes(needle));
  const canType = openVocab && needle && !classes.some((c) => c.toLowerCase() === needle);

  return (
    <div className="panel panel-pad">
      <PanelHead icon={Sparkles} title="Quick sequence"
                 right={<Badge>{openVocab ? "Open vocabulary" : `${classes.length} known objects`}</Badge>} />
      <div className="faint" style={{ fontSize: 14, lineHeight: 1.55, marginTop: -4, marginBottom: 14 }}>
        Each object becomes one “present it to the camera” step, in order. Useful for a live
        demo before the trained experiment model exists.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 14 }}>
        <div>
          <div className="input-wrap" style={{ marginBottom: 10 }}>
            <Search size={15} />
            <input className="input" placeholder={openVocab ? "Search, or type any object" : "Search objects"}
                   value={q} onChange={(e) => setQ(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter" && canType) { add(q); setQ(""); } }} />
          </div>
          {loadError && <ErrorNote message={loadError} onRetry={load} />}
          <div className="tag-grid" style={{ maxHeight: "44vh", overflowY: "auto", paddingRight: 4 }}>
            {canType && (
              <button className="tag" style={{ borderStyle: "dashed" }} onClick={() => { add(q); setQ(""); }}>
                <Plus size={13} /> Add “{q.trim()}”
              </button>
            )}
            {shown.map((c) => (
              <button key={c} className="tag" onClick={() => add(c)}><Plus size={13} />{c}</button>
            ))}
          </div>
          {!loadError && classes.length > 0 && shown.length === 0 && !canType && (
            <Empty>Nothing matches “{q}”.</Empty>
          )}
        </div>

        <div className="col" style={{ gap: 10 }}>
          <input className="input" placeholder="Sequence name (optional)" value={name}
                 onChange={(e) => setName(e.target.value)} />
          <div className="list" style={{ gap: 6, maxHeight: "36vh", overflowY: "auto", minHeight: 120 }}>
            {seq.length === 0 && <Empty icon={Layers}>Pick objects on the left.<br />They run top to bottom.</Empty>}
            {seq.map((c, i) => (
              <div key={`${c}-${i}`} className="seq-row fade-in">
                <span className="seq-num">{String(i + 1).padStart(2, "0")}</span>
                <span className="ellipsis" style={{ fontSize: 14.5 }}>Present the <b>{c}</b></span>
                <span className="row" style={{ gap: 2 }}>
                  <button className="btn btn-xs btn-ghost btn-icon" onClick={() => move(i, -1)} disabled={i === 0}
                          aria-label="Move up"><ArrowUp size={13} /></button>
                  <button className="btn btn-xs btn-ghost btn-icon" onClick={() => move(i, 1)}
                          disabled={i === seq.length - 1} aria-label="Move down"><ArrowDown size={13} /></button>
                  <button className="btn btn-xs btn-ghost btn-icon" onClick={() => setSeq(seq.filter((_, k) => k !== i))}
                          aria-label="Remove"><X size={13} /></button>
                </span>
              </div>
            ))}
          </div>
          {msg && <Note tone="alert">{msg}</Note>}
          <button className="btn btn-primary btn-block" onClick={launch} disabled={!seq.length}>
            <Play size={13} fill="currentColor" /> Run this sequence
          </button>
          <div className="faint" style={{ fontSize: 13, textAlign: "center" }}>
            {seq.length}/15 steps · starts immediately on Mission
          </div>
        </div>
      </div>
    </div>
  );
}
