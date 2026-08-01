#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from drawing_rng.hough_style import learn_hough_model, verify_hough_style
except ModuleNotFoundError:
    BUNDLE = Path(__file__).resolve().parent
    if str(BUNDLE) not in sys.path:
        sys.path.insert(0, str(BUNDLE))
    from hough_style import learn_hough_model, verify_hough_style


def boat(*, wobble: float = 0.0, bow: float = 0.0, phase: float = 0.0):
    vertices = [(0.15, 0.22), (0.85, 0.22), (0.72, 0.72), (0.28, 0.72), (0.15, 0.22)]
    points = []
    for side, (a, b) in enumerate(zip(vertices, vertices[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length
        for index in range(26):
            if side and index == 0:
                continue
            t = index / 25
            offset = wobble * math.sin(2 * math.pi * t + phase + side)
            if side in (0, 2):
                offset += bow * 4 * t * (1 - t) * (1 if side == 0 else -1)
            points.append([a[0] + t * dx + offset * nx, a[1] + t * dy + offset * ny])
    return [points]


def circle():
    return [[
        [0.5 + 0.35 * math.cos(2 * math.pi * index / 120), 0.5 + 0.35 * math.sin(2 * math.pi * index / 120)]
        for index in range(121)
    ]]


def main() -> None:
    references = [
        boat(wobble=0.004, phase=0.0),
        boat(wobble=0.006, phase=0.7),
        boat(wobble=0.005, phase=1.3),
    ]
    model = learn_hough_model(references)
    assert model["applicable"], model

    owner = verify_hough_style(boat(wobble=0.009, phase=2.2), model)
    gentle = verify_hough_style(boat(wobble=0.008, bow=0.025, phase=0.2), model)
    borderline = verify_hough_style(boat(wobble=0.012, bow=0.035, phase=1.7), model)
    bowed = verify_hough_style(boat(wobble=0.005, bow=0.090, phase=0.4), model)
    strongly_bowed = verify_hough_style(boat(wobble=0.005, bow=0.150, phase=0.4), model)
    circle_model = learn_hough_model([circle(), circle(), circle()])

    print("ordinary_wobble", owner["pass"], round(float(owner["score"]), 3))
    print("gentle_variation", gentle["pass"], round(float(gentle["score"]), 3))
    print("borderline_owner", borderline["pass"], round(float(borderline["score"]), 3), "bend", borderline.get("bend_violation_count"))
    print("bowed_sides", bowed["pass"], round(float(bowed["score"]), 3), "bend", bowed.get("bend_violation_count"))
    print("strongly_bowed_sides", strongly_bowed["pass"], round(float(strongly_bowed["score"]), 3))
    print("circle_profile_applicable", circle_model["applicable"])

    assert owner["pass"] is True
    assert gentle["pass"] is True
    assert borderline["pass"] is True
    assert bowed["pass"] is False
    assert strongly_bowed["pass"] is False
    assert circle_model["applicable"] is False
    print("Hough residual self-test passed")


if __name__ == "__main__":
    main()
