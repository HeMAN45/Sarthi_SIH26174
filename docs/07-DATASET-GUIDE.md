# Dataset guide — props, footage and labels

The trained detector is the PS deliverable. Everything downstream of it is
built and tested; this document is the part that needs hands, cardboard and an
afternoon.

The tooling assumes nothing that is not in this repo. Every command below runs
today.

---

## 1. The vocabulary is not a choice

Nine detector classes, derived from `procedures/proc_a.yaml` and
`procedures/proc_b.yaml` — never hardcoded, because a second source of truth
drifts and the failure shows up at step seven in front of a jury.

```bash
uv run orbital-har dataset init --root datasets/bas
```

| # | class | object | note |
|---|---|---|---|
| 0 | `outer_box_closed` | outer_box | |
| 1 | `outer_box_open` | outer_box | state |
| 2 | `red_box_closed` | red_box | state, tethered |
| 3 | `red_box_open` | red_box | state, tethered |
| 4 | `sample_vial` | sample_vial | tethered, smallest object |
| 5 | `tether_clip` | tether_clip | smallest object |
| 6 | `tweezers` | tweezers | thin, motion-blurs easily |
| 7 | `yellow_box_closed` | yellow_box | state, tethered |
| 8 | `yellow_box_open` | yellow_box | state, tethered |

Object **states are separate classes** (invariant #8). There is no second
classifier stage deciding open versus closed — the detector decides, which is
why open and closed must be photographed as if they were different objects.

> The docs elsewhere say "11-class vocabulary". That counts `hand` and `glove`,
> which are now derived from pose rather than detection. Nine is correct for the
> detector.

---

## 2. Prop kit

Total cost is near zero and it is the single thing blocking the deliverable.

| Prop | Build from | Notes |
|---|---|---|
| Outer container | A shoebox or similar, lid hinged with tape | Must open and stay open |
| Red box | Small box, spray-paint or red paper | Distinct red; avoid orange |
| Yellow box | Same size as the red one | Distinct yellow; avoid cream |
| Sample vial | Small clear bottle or pill tube | Should fit in the yellow box |
| Tweezers | Any household pair | |
| Tether clip | A carabiner or a bulldog clip | |
| Rack board | A4/A3 card with four ArUco markers | See below |

**The rack board matters as much as the props.** Print `DICT_4X4_50` markers
with ids `0,1,2,3` at the corners in the order top-left, top-right,
bottom-right, bottom-left, plus id `10` at marker position **R** and id `11` at
position **Y**. Default board size is 600 × 400 mm; change it in
`RackLayout` if yours differs. Leave a white margin around every marker or the
detector will not see it.

Keep the red and yellow boxes **visually distinct in shape as well as colour**.
Training augmentation deliberately jitters hue a little, and two identically
shaped boxes that differ only in colour are the pair the model will confuse.

---

## 3. Shot list

Aim for **300+ instances per class** — `dataset stats` flags anything under.
That is roughly 20–30 minutes of footage sampled at a few frames per second,
not 300 hand-taken photographs.

Record in **takes**, and name frames `take03_0147.jpg`. The splitter groups by
the prefix before the frame number, so a whole take lands entirely in train or
entirely in val. This is not cosmetic: adjacent frames are near-identical, and
splitting randomly puts a frame in train and its neighbour in val, which turns
validation mAP into a measure of memorisation.

Cover, in separate takes:

1. **Baseline** — the full PROC-A sequence, normal lighting, camera upright.
2. **Orientation** — the same run with the camera rotated 90°, 180°, 270°.
   Training augments rotation to ±180°, but real rotated footage is what proves
   it. This is the PS's orientation clause.
3. **Lighting** — bright, dim, and one side-lit take with hard shadows.
4. **Occlusion** — hands and forearms crossing the props repeatedly.
5. **States** — each box opened and closed slowly, held at partial angles.
   Partial opening is where the two state classes actually compete; a model
   trained only on fully-open and fully-shut boxes has no idea what to do
   halfway.
6. **Negatives** — the empty rack, and the props off to one side. Frames with
   nothing to detect are valid training data and reduce false positives.

Deliberately include the hard cases. The engine's abstention path
(`UNVERIFIED`) can only be tuned from runs where the system is *wrong*, and
`orbital-har eval` will refuse to fit thresholds on a corpus in which every
verdict is correct.

---

## 4. Pipeline

```bash
# 1. scaffold, and see the class list and prompts
uv run orbital-har dataset init --root datasets/bas

# 2. drop extracted frames into datasets/bas/raw/  (take03_0147.jpg ...)

# 3. zero-shot pre-label, for a human to correct
uv run orbital-har dataset label --root datasets/bas

# 4. correct them. CVAT, Roboflow and labelImg all open YOLO .txt in place.
#    Expect open/closed to need the most work: an open-vocabulary detector
#    cannot judge state, which is precisely why a trained model exists.

# 5. split by clip, then check readiness
uv run orbital-har dataset split --root datasets/bas
uv run orbital-har dataset stats --root datasets/bas

# 6. train
uv run orbital-har train --data datasets/bas/data.yaml --epochs 100

# 7. score it, and persist the model and its calibration
uv run orbital-har eval --weights runs/bas/weights/best.pt \
    --data datasets/bas/data.yaml --save

# 8. export for the edge target
uv run orbital-har export runs/bas/weights/best.pt --format onnx
```

`.autolabel.json` records which frames were machine-labelled and at what
confidence. It is written with `"reviewed": false`. Flip it by hand once a
person has actually been through them — an unreviewed dataset that looks
reviewed is how a model gets trained on its own mistakes.

---

## 5. What to report

Three numbers, and a judge will ask for all three:

- **mAP@50-95 per class.** The weakest class is the story; expect it to be
  `sample_vial` or `tether_clip`, which are small.
- **Step verdict accuracy and alert recall**, from replaying recorded sessions
  through the real engine. A detector with excellent mAP that still calls a
  skipped step complete has failed at the actual job.
- **False alerts per 10 minutes.** PRD NFR-04. This is the one metric that
  cannot be traded away for a better mean — false alerts erode crew trust, and
  a muted assistant has no value.

`orbital-har eval` prints all three, and `--save` writes the model and its
fitted calibration (temperature, τ_complete, τ_abstain, ECE) into the
`models` and `calibrations` tables.

Keep any predicate `min_conf` **below** the fitted `τ_abstain`, or the
"cannot verify" path becomes unreachable (invariant #12). `eval` warns when the
fitted value collides with `DETECTION_FLOOR`.
