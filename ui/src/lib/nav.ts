import type { LucideIcon } from "lucide-react";
import { Archive, BrainCircuit, ListChecks, Radar } from "lucide-react";

/** The four sections, in keyboard order (1-4). */
export const NAV: { to: string; label: string; icon: LucideIcon; end?: boolean }[] = [
  { to: "/", label: "Mission", icon: Radar, end: true },
  { to: "/procedures", label: "Procedures", icon: ListChecks },
  { to: "/models", label: "Models", icon: BrainCircuit },
  { to: "/archive", label: "Archive", icon: Archive },
];
