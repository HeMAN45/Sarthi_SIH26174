/** Colour themes. One token set in theme.css; this only picks which applies,
 *  and remembers the choice on this machine. */

export type Theme = "graphite" | "slate" | "daylight";

export const THEMES: { id: Theme; label: string; hint: string; swatch: [string, string, string] }[] = [
  { id: "graphite", label: "Graphite", hint: "Charcoal and saffron", swatch: ["#2a2d32", "#1a1c1f", "#f39a2e"] },
  { id: "slate", label: "Slate", hint: "Cool blue-grey", swatch: ["#2a3442", "#1b222c", "#45c2d6"] },
  { id: "daylight", label: "Daylight", hint: "Bright rooms, projectors", swatch: ["#ffffff", "#e2e5e9", "#d46a0c"] },
];

const KEY = "sarthi.theme";

export function savedTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    if (THEMES.some((x) => x.id === t)) return t as Theme;
  } catch { /* storage unavailable: fall back to the default */ }
  return "graphite";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  const bar = THEMES.find((x) => x.id === theme)?.swatch[1];
  if (bar) document.querySelector('meta[name="theme-color"]')?.setAttribute("content", bar);
  try { localStorage.setItem(KEY, theme); } catch { /* not persisted; still applied */ }
}
