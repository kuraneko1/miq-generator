#!/usr/bin/env python3
"""Warm-render timing for representative quotes: python scripts/benchmark.py [cycles]"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import miq  # noqa: E402

AVATAR = ROOT / "demo-avatar.png"

CASES = [
    ("小さな発見が、明日を変える。", "classic", "640x360"),
    ("じゃあ8時間ホモになるか 👩🏽‍💻 Discord 8:30", "classic", "640x360"),
    ("「大切な問い」を考えよう。次の発見につながる。", "classic", "640x360"),
    ("مرحبا Discord 123 👩🏽‍💻", "classic", "640x360"),
    (
        "全文を残して小さく表示する。長い引用文でも省略せずに縮小する。" * 12,
        "classic",
        "640x360",
    ),
    ("毎日が学びの機会。", "mirrored", "854x480"),
    ("毎日が学びの機会。", "noir", "854x480"),
    ("問いを立て続けること。", "telop", "1920x1080"),
]


def run(text, layout, size, output):
    miq.render(
        AVATAR,
        text,
        output,
        "投稿者",
        "poster",
        layout=layout,
        size=size,
        font_family=miq.REGISTRY[layout].fonts[0],
    )


def main() -> int:
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    # Each layout at its orientation's default size (landscape 640x360).
    # The on-request cutout runs a network and is timed separately.
    every = [
        ("毎日が学びの機会。Stay curious ✨", name, None)
        for name in miq.LAYOUTS
        if not miq.REGISTRY[name].hidden
    ]
    cases = CASES + every
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        miq.warmup()
        for index, (text, layout, size) in enumerate(cases):
            # Warm imports, font handles and mask caches before timing.
            run(text, layout, size, output / f"{index}.png")
        samples = []
        for _ in range(cycles):
            for index, (text, layout, size) in enumerate(cases):
                start = time.perf_counter()
                run(text, layout, size, output / f"{index}.png")
                elapsed = (time.perf_counter() - start) * 1000
                samples.append((elapsed, layout, index < len(CASES)))
    print("-- representative cases (same as earlier releases)")
    for layout in ("classic", "mirrored", "noir", "telop"):
        group = [v for v, name, core in samples if core and name == layout]
        print(f"{layout:11} n={len(group):3d} median={statistics.median(group):7.1f}ms")
    core = [v for v, _, flag in samples if flag]
    print(f"{'all':11} n={len(core):3d} median={statistics.median(core):7.1f}ms")
    print("-- every layout at its default size")
    for layout in dict.fromkeys(name for _, name, flag in samples if not flag):
        group = [v for v, name, flag in samples if not flag and name == layout]
        orientation = miq.REGISTRY[layout].orientation
        print(f"{layout:13} {orientation:9} median={statistics.median(group):7.1f}ms")
    landscape = [
        v
        for v, name, flag in samples
        if not flag and miq.REGISTRY[name].orientation == "landscape"
    ]
    print(
        f"{'landscape':11} n={len(landscape):3d} median={statistics.median(landscape):7.1f}ms"
    )
    values = [v for v, _, flag in samples if not flag]
    print(
        f"{'all':11} n={len(values):3d} median={statistics.median(values):7.1f}ms"
        f" mean={statistics.mean(values):7.1f}ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
