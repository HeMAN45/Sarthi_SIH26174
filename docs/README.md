# Documentation index

ORBITAL-HAR · SIH26174 · Team Hashira

Read in order for the full picture. Each document states its own scope and links onward.

| # | Document | Answers |
|---|---|---|
| - | [Problem statement](SIH26174-problem-statement.md) | What ISRO actually asked for, verbatim |
| - | [Architecture overview](architecture.md) | The stack and pipeline at a glance |
| 1 | [PRD](01-PRD.md) | Who it's for, what it must do, how we know it's done |
| 2 | [TRD](02-TRD.md) | How it's built - modules, contracts, models, budgets |
| 3 | [App flow](03-APP-FLOW.md) | How it behaves at runtime, in every scenario |
| 4 | [UI/UX brief](04-UIUX-BRIEF.md) | What it looks like and why |
| 5 | [Backend schema](05-BACKEND-SCHEMA.md) | Where data lives and in what shape |
| 6 | [Implementation plan](06-IMPLEMENTATION-PLAN.md) | Who builds what, when, and what to cut |

Project-wide coding rules and invariants live in [CLAUDE.md](../CLAUDE.md) at the repo root.

## Quick reference

**Two procedures**, one shared object vocabulary, so loading the second requires no
retraining - TRD appendix A.

**Eight differentiators** D-01…D-08, each with a scripted demo moment - PRD §8.

**Ten invariants** that must never be violated - CLAUDE.md.

**Descope ladder** for when the schedule slips - implementation plan §12.
