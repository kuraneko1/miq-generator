#!/usr/bin/env python3
"""Install the background-removal model for ``miq 切り抜き`` (once, offline after).

    python scripts/setup_cutout.py            # isnet-anime (176 MB, best on illustrations)
    python scripts/setup_cutout.py u2netp     # 4.6 MB, faster but rougher

Files come from the rembg project's release (Apache-2.0 models) and are
checked against a fixed SHA-256. Without a model, ``miq 切り抜き`` still works
for flat backgrounds.
"""

import hashlib
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from miq_cutout import DEFAULT_MODEL, MODEL_DIR, MODELS  # noqa: E402

BASE = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
SHA256 = {
    "isnet-anime": "f15622d853e8260172812b657053460e20806f04b9e05147d49af7bed31a6e99",
    "u2netp": "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8",
}


def install(name):
    file = MODELS[name][0]
    target = MODEL_DIR / file
    MODEL_DIR.mkdir(exist_ok=True)
    if target.is_file() and _digest(target.read_bytes()) == SHA256[name]:
        print(f"Already installed: {target.name}")
        return
    with urlopen(BASE + file, timeout=300) as response:
        data = response.read(256 * 1024 * 1024)
    if _digest(data) != SHA256[name]:
        raise ValueError(f"配布ファイルのSHA-256が一致しません: {file}")
    with tempfile.NamedTemporaryFile(dir=MODEL_DIR, delete=False) as temporary:
        temporary.write(data)
        path = Path(temporary.name)
    path.replace(target)
    print(f"Installed: {target.name}")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    for name in sys.argv[1:] or [DEFAULT_MODEL]:
        if name not in MODELS:
            raise SystemExit(f"不明なモデル: {name}（{', '.join(MODELS)}）")
        install(name)
