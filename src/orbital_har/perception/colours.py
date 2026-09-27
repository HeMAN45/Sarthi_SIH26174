"""Objects found by their colour: a solid blue cube, a red box, a yellow box.

A plain coloured block is not one of the stock detector's 80 objects, and a
detector trained on it needs photos, boxes and twenty minutes. For a solid,
saturated colour on an ordinary table none of that is needed: the colour itself
finds it, the same way on every frame, with no model at all. That also covers
the problem statement's sample experiment, which is built from red and yellow
boxes.

Only compact, solid blobs count. A shirt, a poster or a patch of sky is the
right colour too, but it is not the shape of a block: it is too big, too
ragged or too thin, and it is dropped.

Nothing here may import from ``orbital_har.reasoning``.
"""

from __future__ import annotations

import cv2
import numpy as np

#: OpenCV hue runs 0-179. Red wraps round the ends, so it has two bands.
HUE: dict[str, tuple[tuple[int, int], ...]] = {
    "red": ((0, 8), (170, 179)),
    "orange": ((9, 20),),
    "yellow": ((21, 34),),
    "green": ((40, 85),),
    "blue": ((95, 128),),
    "purple": ((129, 160),),
}
COLOURS = tuple(HUE)

#: A block is vivid: washed-out or dark pixels are shadows and walls.
MIN_SATURATION = 110
MIN_VALUE = 60
#: A block is neither a speck nor the whole view.
MIN_AREA, MAX_AREA = 0.002, 0.2
#: ...and roughly square-ish and solid: a stripe of sleeve is neither.
MAX_ASPECT = 3.0
MIN_FILL = 0.45


def find(
    frame: np.ndarray, wanted: dict[str, str]
) -> list[tuple[str, float, tuple[float, float, float, float], float]]:
    """``wanted``: class -> colour. Returns (class, confidence, box, area fraction).

    One box per class: the largest block of that colour. Confidence grows with
    how solid the block is, so a hand half covering the cube lowers it rather
    than losing it.
    """
    if not wanted:
        return []
    h, w = frame.shape[:2]
    total = float(h * w)
    hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2HSV)
    kernel = np.ones((5, 5), np.uint8)
    out = []
    for cls, colour in wanted.items():
        mask = np.zeros((h, w), np.uint8)
        for lo, hi in HUE[colour]:
            mask |= cv2.inRange(hsv, (lo, MIN_SATURATION, MIN_VALUE), (hi, 255, 255))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh / total
            if not MIN_AREA <= area <= MAX_AREA:
                continue
            if max(bw / max(bh, 1), bh / max(bw, 1)) > MAX_ASPECT:
                continue
            fill = cv2.contourArea(c) / float(max(bw * bh, 1))
            if fill < MIN_FILL:
                continue
            if best is None or area > best[3]:
                conf = round(min(0.95, 0.45 + 0.55 * fill), 3)
                best = (cls, conf, (float(x), float(y), float(x + bw), float(y + bh)), area)
        if best is not None:
            out.append(best)
    return out
