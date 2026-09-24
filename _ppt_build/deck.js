// ORBITAL-HAR — premium SIH26174 pitch deck (Team Hashira)
const pptxgen = require("pptxgenjs");
const sharp = require("sharp");
const React = require("react");
const RD = require("react-dom/server");
const FA = require("react-icons/fa6");

// ---------- palette ----------
const C = {
  deep: "070B18", panel: "0F1830", panel2: "16223F", stroke: "273350",
  cyan: "2DD4E8", cyanDk: "0E5A6B", mint: "34D399", orange: "FB923C",
  violet: "A78BFA", gold: "FBBF24", text: "EAF1FC", muted: "93A1BD",
  white: "FFFFFF", navy: "0B2545",
};
const OUT = "C:\\SISH_F\\Clippy\\ORBITAL-HAR_SIH26174_Hashira_PREMIUM.pptx";
const FONT = "Calibri";

// ---------- icon rasterizer ----------
async function mkIcon(name, hex, px = 240) {
  const El = FA[name];
  let svg = RD.renderToStaticMarkup(React.createElement(El, { size: px }));
  svg = svg.replace(/currentColor/g, "#" + hex);
  if (!/viewBox/.test(svg)) svg = svg.replace("<svg", `<svg viewBox="0 0 ${px} ${px}"`);
  const buf = await sharp(Buffer.from(svg)).resize(px, px).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}
const ICONS = {};
async function loadIcons() {
  const spec = {
    camera: ["FaCamera", C.white], rotate: ["FaArrowsRotate", C.white],
    eye: ["FaEye", C.white], bus: ["FaShareNodes", C.white],
    brain: ["FaBrain", C.white], yaml: ["FaFileCode", C.white],
    voice: ["FaVolumeHigh", C.white], link: ["FaLink", C.white],
    cast: ["FaTowerBroadcast", C.white], gauge: ["FaGaugeHigh", C.white],
    astro: ["FaUserAstronaut", C.cyan], feather: ["FaFeather", C.white],
    shield: ["FaShieldHalved", C.white], cube: ["FaCube", C.white],
    check: ["FaCircleCheck", C.white], rocket: ["FaRocket", C.white],
    recycle: ["FaRecycle", C.white], globe: ["FaEarthAsia", C.white],
    flask: ["FaFlask", C.white], book: ["FaBookOpen", C.white],
    bolt: ["FaBolt", C.white], chip: ["FaMicrochip", C.white],
    dish: ["FaSatelliteDish", C.cyan], net: ["FaShareNodes", C.cyan],
  };
  for (const k of Object.keys(spec)) ICONS[k] = await mkIcon(spec[k][0], spec[k][1]);
}

// ---------- helpers ----------
const glow = (col, blur = 14, op = 0.65) =>
  ({ type: "outer", color: col, blur, offset: 0, angle: 0, opacity: op });

function stars(slide, list) {
  list.forEach(([x, y, d, t]) => slide.addShape("ellipse", {
    x, y, w: d, h: d, fill: { color: C.white, transparency: t }, line: { type: "none" },
  }));
}
function rings(slide, cx, cy, radii, col) {
  radii.forEach(r => slide.addShape("ellipse", {
    x: cx - r, y: cy - r, w: r * 2, h: r * 2,
    fill: { type: "none" }, line: { color: col, width: 1 },
  }));
}
// icon inside a colored circle, centered horizontally at (cx, top)
function iconCircle(slide, cx, top, d, accent, iconKey, doGlow = false) {
  slide.addShape("ellipse", {
    x: cx - d / 2, y: top, w: d, h: d, fill: { color: accent },
    line: { type: "none" }, ...(doGlow ? { shadow: glow(accent, 10, 0.7) } : {}),
  });
  const i = d * 0.56;
  slide.addImage({ data: ICONS[iconKey], x: cx - i / 2, y: top + (d - i) / 2, w: i, h: i });
}
function txt(slide, t, o) { slide.addText(t, { isTextBox: true, fontFace: FONT, margin: 0, ...o }); }

function header(slide, kicker, title) {
  txt(slide, kicker, { x: 0.55, y: 0.42, w: 9, h: 0.3, fontSize: 12, bold: true,
    color: C.cyan, charSpacing: 3, align: "left" });
  txt(slide, title, { x: 0.53, y: 0.68, w: 9.5, h: 0.7, fontSize: 31, bold: true,
    color: C.text, align: "left" });
  txt(slide, [{ text: "Team Hashira", options: { bold: true, color: C.text, fontSize: 12, breakLine: true } },
    { text: "SIH26174 · ISRO", options: { color: C.muted, fontSize: 10 } }],
    { x: 9.6, y: 0.46, w: 3.18, h: 0.6, align: "right" });
}
function footer(slide, n) {
  txt(slide, "ORBITAL-HAR", { x: 0.55, y: 7.06, w: 3, h: 0.28, fontSize: 8.5,
    color: C.muted, charSpacing: 2 });
  txt(slide, "0" + n + " / 06", { x: 11.4, y: 7.06, w: 1.38, h: 0.28, fontSize: 8.5,
    color: C.muted, align: "right" });
}
// generic card panel
function panel(slide, x, y, w, h, opts = {}) {
  slide.addShape("roundRect", { x, y, w, h, rectRadius: 0.08,
    fill: { color: opts.fill || C.panel }, line: { color: opts.line || C.stroke, width: opts.lw || 1 },
    ...(opts.glow ? { shadow: glow(opts.glow, 12, 0.5) } : {}) });
}
// icon-topped flow node
function node(slide, x, y, w, h, iconKey, title, sub, accent, doGlow = false) {
  panel(slide, x, y, w, h, { fill: doGlow ? C.panel2 : C.panel, line: doGlow ? accent : C.stroke,
    lw: doGlow ? 1.75 : 1, glow: doGlow ? accent : null });
  const d = 0.42;
  iconCircle(slide, x + w / 2, y + 0.09, d, accent, iconKey);
  txt(slide, title, { x: x + 0.05, y: y + 0.53, w: w - 0.1, h: 0.24, fontSize: 11.5,
    bold: true, color: C.text, align: "center" });
  txt(slide, sub, { x: x + 0.05, y: y + 0.75, w: w - 0.1, h: 0.2, fontSize: 8.5,
    color: C.muted, align: "center" });
}
const line = (slide, x, y, w, h, col, arrow = true, flipV = false) =>
  slide.addShape("line", { x, y, w, h, flipV,
    line: { color: col, width: 2, endArrowType: arrow ? "triangle" : "none" } });

// ================= BUILD =================
(async () => {
  await loadIcons();
  const p = new pptxgen();
  p.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
  p.defineSlideMaster({ title: "BG", background: { color: C.deep } });

  const STAR = [[1.1,0.9,0.05,30],[2.4,0.5,0.03,50],[3.6,1.3,0.04,45],[5.1,0.7,0.03,55],
    [11.9,0.8,0.05,35],[12.6,2.1,0.04,50],[0.7,3.1,0.03,55],[12.9,4.4,0.05,40],
    [11.3,5.6,0.03,55],[0.5,6.2,0.04,50],[2.0,6.7,0.03,60],[6.4,0.4,0.03,60],
    [8.1,0.9,0.04,50],[9.7,0.5,0.03,55],[12.2,6.4,0.04,45],[7.0,6.9,0.03,60]];

  // ---------- SLIDE 1 : TITLE ----------
  let s = p.addSlide({ masterName: "BG" });
  stars(s, STAR);
  rings(s, 10.25, 3.5, [1.15, 1.95, 2.75], C.cyanDk);
  // orbiting dish dot
  s.addShape("ellipse", { x: 10.25 - 2.75 - 0.09, y: 3.5 - 0.09, w: 0.18, h: 0.18, fill: { color: C.mint }, line: { type: "none" }, shadow: glow(C.mint, 8, 0.8) });
  s.addShape("ellipse", { x: 12.05, y: 1.85, w: 0.14, h: 0.14, fill: { color: C.orange }, line: { type: "none" } });
  // center hero
  s.addShape("ellipse", { x: 10.25 - 0.85, y: 3.5 - 0.85, w: 1.7, h: 1.7,
    fill: { color: C.panel2 }, line: { color: C.cyan, width: 2 }, shadow: glow(C.cyan, 18, 0.7) });
  s.addImage({ data: ICONS.astro, x: 10.25 - 0.5, y: 3.5 - 0.5, w: 1.0, h: 1.0 });
  s.addImage({ data: ICONS.dish, x: 11.75, y: 5.15, w: 0.55, h: 0.55 });

  txt(s, "SMART INDIA HACKATHON 2026", { x: 0.7, y: 0.72, w: 8, h: 0.32, fontSize: 13,
    bold: true, color: C.cyan, charSpacing: 4 });
  txt(s, "ORBITAL-HAR", { x: 0.63, y: 1.12, w: 8.5, h: 1.1, fontSize: 60, bold: true, color: C.text });
  txt(s, "Offline on-board procedure supervision for BAS experiments",
    { x: 0.7, y: 2.42, w: 7.4, h: 0.5, fontSize: 17, color: C.muted, italic: true });
  // chips
  const chips = ["Fully offline", "Edge AI", "Explainable & auditable"];
  let cx = 0.7;
  chips.forEach(t => {
    const w = 0.28 + t.length * 0.093;
    panel(s, cx, 3.12, w, 0.42, { fill: C.panel, line: C.cyanDk });
    txt(s, t, { x: cx, y: 3.12, w, h: 0.42, fontSize: 11, bold: true, color: C.cyan,
      align: "center", valign: "middle" });
    cx += w + 0.22;
  });
  // PS title
  txt(s, "PROBLEM STATEMENT · SIH26174", { x: 0.7, y: 4.05, w: 7.4, h: 0.28, fontSize: 11,
    bold: true, color: C.cyan, charSpacing: 2 });
  txt(s, "AI Human Activity Recognition for On-board BAS Experiments",
    { x: 0.7, y: 4.32, w: 7.3, h: 0.62, fontSize: 18, bold: true, color: C.text });
  // meta grid
  const cell = (x, y, label, val) => {
    txt(s, label, { x, y, w: 3.6, h: 0.24, fontSize: 9.5, bold: true, color: C.muted, charSpacing: 2 });
    txt(s, val, { x, y: y + 0.23, w: 3.7, h: 0.3, fontSize: 13, bold: true, color: C.text });
  };
  cell(0.7, 5.25, "ORGANISATION", "ISRO · Dept. of Space");
  cell(0.7, 6.05, "TEAM", "Hashira");
  cell(4.35, 5.25, "THEME · CATEGORY", "Space Technology · Software");
  cell(4.35, 6.05, "TEAM ID", "(as registered on portal)");
  footer(s, 1);

  // ---------- SLIDE 2 : PROPOSED SOLUTION ----------
  s = p.addSlide({ masterName: "BG" });
  header(s, "THE IDEA", "Proposed Solution");
  // hook panel (left, full width of left col)
  panel(s, 0.55, 1.5, 7.15, 1.32, { fill: C.panel2, line: C.cyan, lw: 1.5, glow: C.cyan });
  txt(s, "THE IDEA IN ONE LINE", { x: 0.8, y: 1.66, w: 6.6, h: 0.26, fontSize: 10.5,
    bold: true, color: C.cyan, charSpacing: 2 });
  txt(s, "A fixed payload camera watches an astronaut run an experiment — the system recognises each action, prompts the next step, voice-alerts on a skip or wrong order, and emits a few KB of verified telemetry instead of a video downlink.",
    { x: 0.8, y: 1.94, w: 6.65, h: 0.82, fontSize: 12.5, color: C.text, lineSpacingMultiple: 1.0 });

  txt(s, "HOW IT ADDRESSES THE PROBLEM", { x: 0.55, y: 3.05, w: 7, h: 0.28, fontSize: 11,
    bold: true, color: C.muted, charSpacing: 2 });
  const rows = [
    ["feather", C.cyan, "Lightweight downlink", "A few KB of hash-chained step telemetry replaces high-bandwidth video — provable status without real-time comms."],
    ["brain", C.mint, "Reasoning-first, never guesses", "A deterministic state machine decides; below confidence it reports UNVERIFIED rather than a confident wrong verdict."],
    ["rotate", C.violet, "Orientation-agnostic", "The input frame is canonicalised to the payload rack (ArUco), so a floating or inverted astronaut is still recognised."],
  ];
  let ry = 3.42;
  rows.forEach(([ic, ac, t, d]) => {
    iconCircle(s, 0.85, ry, 0.5, ac, ic);
    txt(s, t, { x: 1.28, y: ry - 0.02, w: 6.35, h: 0.28, fontSize: 12.5, bold: true, color: C.text });
    txt(s, d, { x: 1.28, y: ry + 0.26, w: 6.4, h: 0.62, fontSize: 10.5, color: C.muted, lineSpacingMultiple: 0.98 });
    ry += 1.08;
  });

  // right column: innovation cards
  txt(s, "INNOVATION & UNIQUENESS", { x: 7.95, y: 1.5, w: 5, h: 0.28, fontSize: 11,
    bold: true, color: C.muted, charSpacing: 2 });
  const inn = [
    ["cube", C.gold, "Procedure-as-config", "Every experiment is a YAML file, not code — a new experiment is a new file, engine untouched."],
    ["net", C.cyan, "Event-bus seam", "Perception and reasoning are decoupled: record once, replay 1000× — and live demo insurance if a camera fails."],
    ["shield", C.mint, "Explainable & auditable", "Deterministic verdicts plus an append-only, hash-chained log make every decision defensible."],
  ];
  let iy = 1.86;
  inn.forEach(([ic, ac, t, d]) => {
    panel(s, 7.95, iy, 4.83, 1.5);
    iconCircle(s, 8.4, iy + 0.26, 0.56, ac, ic);
    txt(s, t, { x: 8.9, y: iy + 0.2, w: 3.7, h: 0.3, fontSize: 13, bold: true, color: C.text });
    txt(s, d, { x: 8.9, y: iy + 0.53, w: 3.75, h: 0.85, fontSize: 10.5, color: C.muted, lineSpacingMultiple: 0.98 });
    iy += 1.66;
  });
  footer(s, 2);

  // ---------- SLIDE 3 : TECHNICAL APPROACH ----------
  s = p.addSlide({ masterName: "BG" });
  header(s, "ARCHITECTURE", "Technical Approach");
  // tech stack panel (top)
  panel(s, 0.55, 1.42, 12.23, 0.95);
  const tech = [
    ["PERCEPTION", "YOLO11 · MediaPipe Hands · YOLO11-pose · OpenCV ArUco"],
    ["REASONING", "Pydantic + YAML procedures · deterministic state machine · 7 predicate evaluators"],
    ["RUNTIME", "Piper offline TTS · FastAPI + WebSocket · React + Vite (bundled) · FFmpeg + MediaMTX"],
    ["EDGE", "Jetson Orin Nano · TensorRT export · Blender synthetic data · zero network at runtime"],
  ];
  let ty = 1.5;
  tech.forEach(([k, v]) => {
    txt(s, [{ text: k + "   ", options: { bold: true, color: C.cyan } },
      { text: v, options: { color: C.text } }],
      { x: 0.78, y: ty, w: 11.8, h: 0.21, fontSize: 10.3 });
    ty += 0.215;
  });

  // ----- diagram -----
  const ROW = 2.62, BH = 0.95, W = 2.15, G = 0.30, X0 = 0.55;
  const xs = [0,1,2,3,4].map(i => X0 + i * (W + G));
  node(s, xs[0], ROW, W, BH, "camera", "Payload Camera", "+ ArUco rack", C.cyan);
  node(s, xs[1], ROW, W, BH, "rotate", "Rack-frame", "canonicalizer", C.violet);
  node(s, xs[2], ROW, W, BH, "eye", "Perception", "detect · hands · pose", C.violet);
  node(s, xs[3], ROW, W, BH, "bus", "Event Bus", "JSONL stream", C.cyan, true);
  node(s, xs[4], ROW, W, BH, "brain", "Procedure Engine", "deterministic FSM", C.mint, true);
  for (let i = 0; i < 4; i++) line(s, xs[i] + W + 0.02, ROW + BH / 2, G - 0.04, 0, C.cyan);

  // YAML feeds engine (full-height node so its sub-label sits inside the box)
  const yamlY = 3.9;
  node(s, 8.9, yamlY, 2.3, 0.95, "yaml", "Procedure YAML", "experiment as config", C.gold);
  line(s, 10.2, ROW + BH + 0.02, 0, yamlY - (ROW + BH) - 0.02, C.gold, true, true);
  // engine -> manifold
  const barY = 5.2, dropX = 11.36;
  line(s, dropX, ROW + BH + 0.02, 0, barY - (ROW + BH) - 0.02, C.cyan);
  // outputs
  const oW = 2.85, oG = 0.28, oX = 0.55, oY = 5.45, oH = 0.9;
  const oc = [0,1,2,3].map(i => oX + i * (oW + oG) + oW / 2);
  s.addShape("line", { x: oc[0], y: barY, w: oc[3] - oc[0], h: 0, line: { color: C.cyan, width: 2.25 } });
  oc.forEach(c => line(s, c, barY + 0.02, 0, oY - barY - 0.04, C.cyan));
  const outs = [
    ["voice", C.orange, "Voice Alerts", "Piper · offline"],
    ["link", C.mint, "Telemetry", "hash-chained · KB"],
    ["cast", C.cyan, "RTSP + Local", "stream & record"],
    ["gauge", C.violet, "Dashboard", "Crew HUD / Ground"],
  ];
  outs.forEach(([ic, ac, t, sub], i) => node(s, oX + i * (oW + oG), oY, oW, oH, ic, t, sub, ac));
  txt(s, "Deterministic engine emits — fire-and-forget: losing any output never stops supervision.",
    { x: 0.55, y: 6.52, w: 12.2, h: 0.26, fontSize: 9.5, italic: true, color: C.muted });
  footer(s, 3);

  // ---------- SLIDE 4 : FEASIBILITY ----------
  s = p.addSlide({ masterName: "BG" });
  header(s, "CAN WE BUILD IT?", "Feasibility & Viability");
  txt(s, "ALREADY DE-RISKED", { x: 0.55, y: 1.5, w: 5, h: 0.28, fontSize: 11, bold: true,
    color: C.muted, charSpacing: 2 });
  const stats = [
    [C.cyan, "73", "tests passing", "Reasoning spine proven end-to-end with no camera and no models."],
    [C.mint, "LIVE", "webcam demo today", "A pretrained YOLO stand-in already feeds the real engine — HUD + voice."],
    [C.gold, "~2,800", "frames to train", "≈2,000 synthetic + 800 real; fixed camera, <12 classes = small-data regime."],
  ];
  let sy = 1.86;
  stats.forEach(([ac, big, lab, d]) => {
    panel(s, 0.55, sy, 5.55, 1.42);
    txt(s, big, { x: 0.75, y: sy + 0.2, w: 1.85, h: 1.0, fontSize: 40, bold: true, color: ac, align: "center", valign: "middle" });
    txt(s, lab, { x: 2.65, y: sy + 0.22, w: 3.3, h: 0.3, fontSize: 13.5, bold: true, color: C.text });
    txt(s, d, { x: 2.65, y: sy + 0.56, w: 3.35, h: 0.72, fontSize: 10.3, color: C.muted, lineSpacingMultiple: 0.98 });
    sy += 1.56;
  });

  txt(s, "RISKS  →  MITIGATIONS", { x: 6.5, y: 1.5, w: 6, h: 0.28, fontSize: 11, bold: true,
    color: C.muted, charSpacing: 2 });
  const rm = [
    ["Dataset generation is the critical path", "Blender auto-labelled synthetic data, randomised across the full orientation sphere"],
    ["Pose models fail on inverted bodies", "Canonicalise the input frame, not the output pose — model sees an upright person"],
    ["Real-time latency in the alert path", "Pre-synthesize voice prompts to WAV; TensorRT export on the Jetson"],
    ["Camera fails during the live demo", "Event-bus replay of a recorded session — the demo keeps running"],
  ];
  let my = 1.86;
  rm.forEach(([risk, mit]) => {
    panel(s, 6.5, my, 6.28, 1.16);
    s.addShape("ellipse", { x: 6.72, y: my + 0.22, w: 0.16, h: 0.16, fill: { color: C.orange }, line: { type: "none" } });
    txt(s, risk, { x: 6.98, y: my + 0.13, w: 5.65, h: 0.34, fontSize: 11.5, bold: true, color: C.text });
    s.addImage({ data: ICONS.check, x: 6.72, y: my + 0.6, w: 0.22, h: 0.22 });
    txt(s, mit, { x: 7.02, y: my + 0.57, w: 5.6, h: 0.5, fontSize: 10.3, color: C.mint, lineSpacingMultiple: 0.95 });
    my += 1.28;
  });
  footer(s, 4);

  // ---------- SLIDE 5 : IMPACT ----------
  s = p.addSlide({ masterName: "BG" });
  header(s, "WHY IT MATTERS", "Impact & Benefits");
  // hero bandwidth stat (left)
  panel(s, 0.55, 1.62, 4.35, 4.9, { fill: C.panel2, line: C.cyan, lw: 1.5, glow: C.cyan });
  iconCircle(s, 2.72, 2.0, 0.7, C.cyan, "feather", true);
  txt(s, "GB  →  KB", { x: 0.75, y: 2.95, w: 3.95, h: 1.0, fontSize: 46, bold: true, color: C.cyan, align: "center" });
  txt(s, "DOWNLINK PER SESSION", { x: 0.75, y: 3.95, w: 3.95, h: 0.3, fontSize: 12, bold: true, color: C.text, align: "center", charSpacing: 2 });
  txt(s, "Verified, hash-chained step telemetry replaces raw video — an orders-of-magnitude saving on a bandwidth-restricted space link.",
    { x: 0.85, y: 4.4, w: 3.75, h: 1.4, fontSize: 12, color: C.muted, align: "center", lineSpacingMultiple: 1.05 });

  const ben = [
    ["rocket", C.orange, "Mission autonomy", "Protocol-perfect science execution beyond real-time ground support — the core need of the PS."],
    ["shield", C.mint, "Safety & trust", "Deterministic, tamper-evident audit of every step reduces crew error and supports post-mission review."],
    ["recycle", C.violet, "Reusable platform", "Procedure-as-config generalises to surgical, industrial, hazardous-site and training tasks."],
    ["globe", C.cyan, "National capability", "Indigenous, fully-offline edge-AI aligned with India's BAS and lunar programme."],
  ];
  const bx = [5.15, 9.02], by = [1.62, 4.09], bW = 3.76, bH = 2.33;
  ben.forEach(([ic, ac, t, d], i) => {
    const x = bx[i % 2], y = by[Math.floor(i / 2)];
    panel(s, x, y, bW, bH);
    iconCircle(s, x + 0.55, y + 0.32, 0.6, ac, ic);
    txt(s, t, { x: x + 0.28, y: y + 1.02, w: bW - 0.5, h: 0.34, fontSize: 14, bold: true, color: C.text });
    txt(s, d, { x: x + 0.28, y: y + 1.4, w: bW - 0.52, h: 0.8, fontSize: 10.8, color: C.muted, lineSpacingMultiple: 1.0 });
  });
  footer(s, 5);

  // ---------- SLIDE 6 : REFERENCES + CLOSE ----------
  s = p.addSlide({ masterName: "BG" });
  header(s, "SOURCES", "Research & References");
  const refs = [
    ["book", "SIH 2026 · SIH26174 — AI HAR for On-board BAS Experiments, ISRO / Dept. of Space", "sih.gov.in/sih2026PS"],
    ["eye", "Ultralytics YOLO11 / YOLO-World — detection & open-vocabulary pre-labelling", "docs.ultralytics.com"],
    ["cube", "Google MediaPipe Hands — real-time hand landmarks & hand-object interaction", "mediapipe.dev"],
    ["rotate", "OpenCV ArUco — 6-DoF fiducial marker pose for rack-frame canonicalisation", "docs.opencv.org"],
    ["voice", "Piper TTS (Rhasspy) — fully offline neural text-to-speech for voice alerts", "github.com/rhasspy/piper"],
    ["chip", "Orientation-agnostic 3D Human Mesh Recovery (HMR) — rack-relative body tracking", "research literature"],
    ["flask", "Blender bpy — scripted synthetic-data generation with automatic labels", "docs.blender.org"],
  ];
  let fy = 1.5;
  refs.forEach(([ic, t, src]) => {
    iconCircle(s, 0.85, fy, 0.4, C.cyanDk, ic);
    txt(s, t, { x: 1.28, y: fy - 0.02, w: 8.9, h: 0.42, fontSize: 11.5, color: C.text, valign: "middle" });
    txt(s, src, { x: 10.25, y: fy - 0.02, w: 2.55, h: 0.42, fontSize: 10, italic: true, color: C.cyan, align: "right", valign: "middle" });
    fy += 0.62;
  });
  // closing band
  panel(s, 0.55, 6.02, 12.23, 0.92, { fill: C.panel2, line: C.cyan, lw: 1.25, glow: C.cyan });
  txt(s, [{ text: "The reasoning spine is done and tested — the trained AI model is the next milestone. ",
      options: { color: C.text } },
    { text: "ORBITAL-HAR turns any procedure into a supervised, auditable, offline mission asset.",
      options: { color: C.cyan, bold: true } }],
    { x: 0.85, y: 6.14, w: 8.6, h: 0.68, fontSize: 12.5, valign: "middle", lineSpacingMultiple: 1.0 });
  txt(s, [{ text: "Team Hashira", options: { bold: true, color: C.text, fontSize: 13, breakLine: true } },
    { text: "SIH26174 · ISRO", options: { color: C.muted, fontSize: 10 } }],
    { x: 9.7, y: 6.2, w: 2.9, h: 0.6, align: "right", valign: "middle" });
  footer(s, 6);

  await p.writeFile({ fileName: OUT });
  console.log("SAVED", OUT);
})();
