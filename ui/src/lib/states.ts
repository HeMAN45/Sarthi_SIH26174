import type { LucideIcon } from "lucide-react";
import { ArrowLeftRight, Check, CircleQuestionMark, Hand, Hourglass, X } from "lucide-react";
import type { StepState } from "./api";

/** Every state carries an icon and a word alongside its colour, so the screen
 *  still reads on a washed-out projector and for anyone who does not separate
 *  red from green (UI brief §6). */
export const STATE_META: Record<
  StepState,
  { label: string; color: string; soft: string; line: string; icon: LucideIcon | null; done: boolean }
> = {
  pending:      { label: "Pending",       color: "var(--ink-3)",   soft: "transparent",         line: "var(--line-3)",       icon: null,               done: false },
  active:       { label: "In progress",   color: "var(--accent)",  soft: "var(--accent-soft)",  line: "var(--accent)",       icon: null,               done: false },
  complete:     { label: "Verified",      color: "var(--ok)",      soft: "var(--ok-soft)",      line: "var(--ok-line)",      icon: Check,              done: true },
  skipped:      { label: "Skipped",       color: "var(--alert)",   soft: "var(--alert-soft)",   line: "var(--alert-line)",   icon: X,                  done: true },
  out_of_order: { label: "Out of order",  color: "var(--alert)",   soft: "var(--alert-soft)",   line: "var(--alert-line)",   icon: ArrowLeftRight,     done: true },
  unverified:   { label: "Unverified",    color: "var(--caution)", soft: "var(--caution-soft)", line: "var(--caution-line)", icon: CircleQuestionMark, done: false },
  overridden:   { label: "Crew override", color: "var(--info)",    soft: "var(--info-soft)",    line: "var(--info-line)",    icon: Hand,               done: true },
  stalled:      { label: "Stalled",       color: "var(--caution)", soft: "var(--caution-soft)", line: "var(--caution-line)", icon: Hourglass,          done: false },
};

export const meta = (s: string) => STATE_META[s as StepState] ?? STATE_META.pending;
