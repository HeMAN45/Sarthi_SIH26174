import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { LiveState } from "../lib/api";
import { Empty, ErrorNote, SectionTitle } from "../components/Bits";

export default function Experiment({
  state, resetSpeech,
}: { state: LiveState | null; resetSpeech: () => void }) {
  const [classes, setClasses] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");
  const [q, setQ] = useState("");
  const [seq, setSeq] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [custom, setCustom] = useState("");
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState(false);

  const openVocab = !!state?.open_vocab;

  // A swallowed error here leaves an empty panel that looks like a missing
  // feature. Say what failed and offer the retry.
  const load = useCallback(() => {
    setLoadError("");
    api.classes()
      .then(setClasses)
      .catch((e) => setLoadError(`Could not load the object list: ${e.message ?? e}`));
  }, []);

  useEffect(load, [load]);

  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= seq.length) return;
    const n = [...seq];
    [n[i], n[j]] = [n[j], n[i]];
    setSeq(n);
  };

  const add = (c: string) => setSeq((prev) => [...prev, c]);

  const start = async () => {
    setMsg("");
    if (!seq.length) { setOk(false); setMsg("Add at least one object first."); return; }
    resetSpeech();
    try {
      const r = await api.build(seq, name || undefined);
      setOk(!!r.ok);
      setMsg(r.ok
        ? `Started “${name || "Custom experiment"}” — open Mission to run it.`
        : r.error ?? "The server rejected that sequence.");
    } catch (e) {
      setOk(false);
      setMsg(`Could not start: ${(e as Error).message}`);
    }
  };

  const needle = q.trim().toLowerCase();
  const shown = classes.filter((c) => c.toLowerCase().includes(needle));

  return (
    <div className="page">
      <div className="split">
        {/* ------------------------------------------------------- objects */}
        <div className="panel panel-pad">
          <SectionTitle right={<span className="chip chip-idle mono">{classes.length} known</span>}>
            Objects · click to add
          </SectionTitle>

          {loadError && <ErrorNote message={loadError} onRetry={load} />}

          {openVocab && (
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <input
                className="input"
                placeholder="type ANY object — e.g. wrench, red box"
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && custom.trim()) { add(custom.trim()); setCustom(""); }
                }}
              />
              <button className="btn btn-primary"
                      onClick={() => { if (custom.trim()) { add(custom.trim()); setCustom(""); } }}>
                Add
              </button>
            </div>
          )}

          <input className="input" placeholder="search objects…" value={q}
                 onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 12 }} />

          <div className="chips-row" style={{ maxHeight: "48vh", overflowY: "auto" }}>
            {shown.map((c) => (
              <button key={c} className="tag-btn" onClick={() => add(c)}>{c}</button>
            ))}
          </div>

          {!loadError && shown.length === 0 && (
            <Empty>
              {classes.length === 0
                ? "The detector reported no classes."
                : `Nothing matches “${q}”.`}
            </Empty>
          )}

          {openVocab && (
            <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 12 }}>
              Open-vocabulary detector active — the list above is a shortcut, not a limit.
              Any object you can name works.
            </div>
          )}
        </div>

        {/* ------------------------------------------------------ sequence */}
        <div className="panel panel-pad">
          <SectionTitle
            right={<span className="chip chip-idle mono">{seq.length} steps</span>}
          >
            Sequence · steps run in this order
          </SectionTitle>

          <input className="input" placeholder="experiment name (optional)" value={name}
                 onChange={(e) => setName(e.target.value)} style={{ marginBottom: 12 }} />

          <div style={{ maxHeight: "46vh", overflowY: "auto", marginBottom: 12 }}>
            {seq.length === 0 && <Empty>No objects yet — pick some on the left.</Empty>}
            {seq.map((c, i) => (
              <div key={`${c}-${i}`} className="row fade-in">
                <span className="mono" style={{ color: "var(--accent)", width: 18 }}>{i + 1}</span>
                <span style={{ flex: 1, fontSize: 13.5 }}>{c}</span>
                <button className="btn" style={{ padding: "3px 8px" }}
                        onClick={() => move(i, -1)} disabled={i === 0}>▲</button>
                <button className="btn" style={{ padding: "3px 8px" }}
                        onClick={() => move(i, 1)} disabled={i === seq.length - 1}>▼</button>
                <button className="btn btn-alert" style={{ padding: "3px 8px" }}
                        onClick={() => setSeq(seq.filter((_, k) => k !== i))}>✕</button>
              </div>
            ))}
          </div>

          {msg && (
            <div style={{ fontSize: 12.5, marginBottom: 10,
                          color: ok ? "var(--accent)" : "var(--alert)" }}>
              {ok ? "✓" : "✗"} {msg}
            </div>
          )}

          <button className="btn btn-primary" style={{ width: "100%" }} onClick={start}>
            ▶ Start this experiment
          </button>
        </div>
      </div>
    </div>
  );
}
