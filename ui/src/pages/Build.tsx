import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { LiveState } from "../lib/api";
import { Empty, SectionTitle } from "../components/Bits";

export default function Build({
  state, resetSpeech,
}: { state: LiveState | null; resetSpeech: () => void }) {
  const [classes, setClasses] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [seq, setSeq] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [custom, setCustom] = useState("");
  const [msg, setMsg] = useState("");

  const openVocab = !!state?.open_vocab;

  useEffect(() => { api.classes().then(setClasses).catch(() => {}); }, []);

  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= seq.length) return;
    const n = [...seq];
    [n[i], n[j]] = [n[j], n[i]];
    setSeq(n);
  };

  const start = async () => {
    setMsg("");
    if (!seq.length) return setMsg("Add at least one object.");
    resetSpeech();
    const r = await api.build(seq, name || undefined);
    setMsg(r.ok ? `✓ Started "${name || "Custom experiment"}" — open Mission` : `✗ ${r.error}`);
  };

  const shown = classes.filter((c) => c.includes(q.toLowerCase()));

  return (
    <div className="page">
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        <div className="card card-pad">
          <SectionTitle
            right={<span className="pill">{classes.length} known</span>}
          >
            Objects · click to add
          </SectionTitle>

          {openVocab && (
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <input
                className="input" placeholder="type ANY object — e.g. wrench, red box"
                value={custom} onChange={(e) => setCustom(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && custom.trim()) {
                    setSeq([...seq, custom.trim()]); setCustom("");
                  }
                }}
              />
              <button className="btn btn-primary"
                onClick={() => { if (custom.trim()) { setSeq([...seq, custom.trim()]); setCustom(""); } }}>
                Add
              </button>
            </div>
          )}

          <input className="input" placeholder="search objects…" value={q}
                 onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 12 }} />

          <div style={{ display: "flex", flexWrap: "wrap", gap: 7, maxHeight: "52vh", overflow: "auto" }}>
            {shown.map((c) => (
              <button key={c} className="btn" style={{ padding: "6px 12px", fontSize: 12, borderRadius: 20 }}
                      onClick={() => setSeq([...seq, c])}>
                {c}
              </button>
            ))}
          </div>
          {openVocab && (
            <div style={{ fontSize: 11.5, color: "var(--dim)", marginTop: 12 }}>
              Open-vocabulary detector active — the list above is only a shortcut; any object
              you can name works.
            </div>
          )}
        </div>

        <div className="card card-pad">
          <SectionTitle>Sequence · steps run in this order</SectionTitle>
          <input className="input" placeholder="experiment name (optional)"
                 value={name} onChange={(e) => setName(e.target.value)}
                 style={{ marginBottom: 12 }} />

          <div style={{ maxHeight: "46vh", overflow: "auto", marginBottom: 12 }}>
            {seq.length === 0 && <Empty>No objects yet — pick some on the left.</Empty>}
            {seq.map((c, i) => (
              <div key={`${c}-${i}`} className="fade-in"
                   style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 10px",
                            background: "#0d141d", border: "1px solid var(--line)",
                            borderRadius: "var(--r-sm)", marginBottom: 7 }}>
                <span className="mono" style={{ color: "var(--accent)", width: 18 }}>{i + 1}</span>
                <span style={{ flex: 1, fontSize: 13.5 }}>{c}</span>
                <button className="btn" style={{ padding: "3px 8px" }} onClick={() => move(i, -1)}>▲</button>
                <button className="btn" style={{ padding: "3px 8px" }} onClick={() => move(i, 1)}>▼</button>
                <button className="btn btn-danger" style={{ padding: "3px 8px" }}
                        onClick={() => setSeq(seq.filter((_, k) => k !== i))}>✕</button>
              </div>
            ))}
          </div>

          {msg && <div style={{ fontSize: 12.5, marginBottom: 10,
                                color: msg.startsWith("✓") ? "var(--ok)" : "var(--bad)" }}>{msg}</div>}
          <button className="btn btn-ok" style={{ width: "100%", justifyContent: "center" }}
                  onClick={start}>
            ▶ Start this experiment
          </button>
        </div>
      </div>
    </div>
  );
}
