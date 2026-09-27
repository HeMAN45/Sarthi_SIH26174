import type { LucideIcon } from "lucide-react";
import {
  ArrowDownToLine, ArrowUpFromLine, CircleUserRound, GlassWater, Hand, HandMetal, Handshake,
  MoveHorizontal, MoveUpRight, PersonStanding, Sparkle, WavesHorizontal, X,
} from "lucide-react";

export type GestureGroup = "hand" | "both" | "motion";

/** Body actions SARTHI reads from pose, in the operator's own frame of
 *  reference - they read the same standing, lying or upside down. Mirrors
 *  GESTURES in core/types.py. */
export const GESTURES: {
  id: string; label: string; hint: string; icon: LucideIcon; group: GestureGroup; oneHand: boolean;
}[] = [
  { id: "hand_raised", label: "Raise a hand", hint: "Forearm up, like asking a question", icon: Hand, group: "hand", oneHand: true },
  { id: "hand_to_face", label: "Hand to face", hint: "At the mouth or face - drinking, eating", icon: GlassWater, group: "hand", oneHand: true },
  { id: "hand_on_head", label: "Hand on head", hint: "Elbow up, hand beside the head", icon: CircleUserRound, group: "hand", oneHand: true },
  { id: "reaching", label: "Reach out", hint: "Arm straight, lifted away from the body", icon: MoveUpRight, group: "hand", oneHand: true },
  { id: "both_hands_raised", label: "Raise both hands", hint: "Both forearms up", icon: HandMetal, group: "both", oneHand: false },
  { id: "hands_together", label: "Hands together", hint: "Both hands close - twisting a cap", icon: Handshake, group: "both", oneHand: false },
  { id: "arms_crossed", label: "Cross your arms", hint: "Each hand past the middle of the chest", icon: X, group: "both", oneHand: false },
  { id: "arms_out", label: "Arms out to the sides", hint: "A T: both arms straight, shoulder height", icon: MoveHorizontal, group: "both", oneHand: false },
  { id: "hands_on_hips", label: "Hands on hips", hint: "Standing, knees in view", icon: PersonStanding, group: "both", oneHand: false },
  { id: "waving", label: "Wave", hint: "A raised hand going side to side", icon: WavesHorizontal, group: "motion", oneHand: true },
  { id: "lifting", label: "Lift", hint: "The hand rises - lifting what it holds", icon: ArrowUpFromLine, group: "motion", oneHand: true },
  { id: "lowering", label: "Lower", hint: "The hand comes down - setting it down", icon: ArrowDownToLine, group: "motion", oneHand: true },
  { id: "clapping", label: "Clap", hint: "Hands meeting again and again - slowly", icon: Sparkle, group: "motion", oneHand: false },
];

export const GROUP_LABEL: Record<GestureGroup, string> = {
  hand: "One hand",
  both: "Both hands · body",
  motion: "Movements",
};

export const gestureLabel = (id: string | null | undefined): string =>
  GESTURES.find((g) => g.id === id)?.label ?? (id ?? "");

export const isOneHanded = (id: string | null | undefined): boolean =>
  !!GESTURES.find((g) => g.id === id)?.oneHand;

/** What a step does with its object. */
export const HOWS: { id: string; label: string }[] = [
  { id: "show", label: "Show it" },
  { id: "hold", label: "Hold it" },
  { id: "pour", label: "Pour / tip it" },
  { id: "move", label: "Move it" },
];

/** Default wording, mirroring runtime/experiments.py so the placeholder shows
 *  exactly what will be spoken when the operator leaves the step blank. */
const PHRASE: Record<string, string> = {
  hand_raised: "Raise your hand",
  hand_to_face: "Bring your hand to your face",
  hand_on_head: "Put your hand on your head",
  reaching: "Reach out",
  waving: "Wave your hand",
  lifting: "Lift your hand",
  lowering: "Lower your hand",
  both_hands_raised: "Raise both hands",
  hands_together: "Bring your hands together",
  arms_crossed: "Cross your arms",
  arms_out: "Stretch both arms out to the sides",
  hands_on_hips: "Put your hands on your hips",
  clapping: "Clap your hands",
};
const MOTION_WITH: Record<string, string> = { lifting: "Lift", lowering: "Lower", waving: "Wave" };
const HOW_VERB: Record<string, string> = { show: "Present", hold: "Pick up", pour: "Pour from", move: "Move" };

function withHand(phrase: string, hand: string): string {
  if (hand === "any") return phrase;
  if (phrase.includes("your hand")) return phrase.replace("your hand", `your ${hand} hand`);
  return `${phrase} with your ${hand} hand`;
}

export function defaultWording(
  object?: string | null, gesture?: string | null, hand = "any", how = "show",
): string {
  if (object && gesture) {
    if (how === "show") return `${withHand(PHRASE[gesture] ?? gesture, hand)} with the ${object}`;
    if (MOTION_WITH[gesture]) return withHand(`${MOTION_WITH[gesture]} the ${object}`, hand);
    return `${withHand(PHRASE[gesture] ?? gesture, hand)} holding the ${object}`;
  }
  if (gesture) return withHand(PHRASE[gesture] ?? gesture, hand);
  if (object) return how === "show" ? `Present the ${object}` : withHand(`${HOW_VERB[how]} the ${object}`, hand);
  return "";
}
