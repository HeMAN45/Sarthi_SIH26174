/** Run modes and detector naming, shared by Mission, Models and the HUD. */

export type Mode = "clean" | "strict";

export const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "clean", label: "Clean" },
  { value: "strict", label: "Strict" },
];

export const MODE_HINT: Record<Mode, string> = {
  clean: "Each step is judged as it is done. A step done early counts, and the one skipped is alerted.",
  strict: "A step done ahead of its turn is held out of sequence until the skipped one is done.",
};

export function detectorLabel(mode: string | undefined, trained: string[] = []): string {
  if (mode === "classifier") return "Trained classifier";
  if (mode === "open-vocab") return "Open-vocabulary";
  if (mode === "detector") return `COCO-80 + ${trained.length || "your"} trained`;
  return "Stand-in (COCO-80)";
}
