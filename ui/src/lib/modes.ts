/** Run modes and detector naming, shared by Mission, Models and the HUD. */

export type Mode = "clean" | "strict";

export const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "clean", label: "Clean" },
  { value: "strict", label: "Strict" },
];

export const MODE_HINT: Record<Mode, string> = {
  clean: "Standard supervision: each step is judged as it is done.",
  strict: "Also flags steps done ahead of their preconditions as out of sequence.",
};

export function detectorLabel(mode: string | undefined): string {
  if (mode === "classifier") return "Trained classifier";
  if (mode === "open-vocab") return "Open-vocabulary";
  return "Stand-in (COCO-80)";
}
