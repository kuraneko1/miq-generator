"""Cut the subject (a character, a face) out of an icon for ``miq 切り抜き``.

``cutout(image)`` returns the image with a transparent background, cropped to
the subject. With ``onnxruntime`` and a model from ``scripts/setup_cutout.py``
it uses a segmentation network (``isnet-anime`` by default: very good on
illustrated avatars, about 1–2 s on a CPU; ``MIQ_CUTOUT_MODEL=u2netp`` is a
5 MB model that runs several times faster but with rougher edges).

Without them it falls back to separating the border colour from the rest,
which works for flat backgrounds. Masks are cached per image, so the 📐/🎲
redraws of the same quote do not run the network again.
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

MODEL_DIR = Path(__file__).resolve().parent / "models"
# name -> (file, input side, mean, std)
MODELS = {
    "isnet-anime": ("isnet-anime.onnx", 1024, (0.5, 0.5, 0.5), (1.0, 1.0, 1.0)),
    "u2netp": ("u2netp.onnx", 320, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
}
DEFAULT_MODEL = "isnet-anime"
# The network sees at most this many pixels per side of the source.
SOURCE_SIDE = 1024
_CACHE: OrderedDict[str, Image.Image] = OrderedDict()
_CACHE_SIZE = 24


def model_name():
    name = os.getenv("MIQ_CUTOUT_MODEL", DEFAULT_MODEL)
    return name if name in MODELS else DEFAULT_MODEL


def model_path(name=None):
    """The model file to use, or ``None`` (fall back to the border method)."""

    preferred = name or model_name()
    for key in (preferred, *MODELS):
        path = MODEL_DIR / MODELS[key][0]
        if path.is_file():
            return key, path
    return None


@lru_cache(maxsize=2)
def _session(path):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def available():
    """True when the network (not just the fallback) can run."""

    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return model_path() is not None


def preload():
    """Load the network now (a bot worker's first cutout then runs at full speed)."""

    if available():
        _session(model_path()[1])


def _network_mask(image):
    import numpy as np

    found = model_path()
    if found is None:
        return None
    key, path = found
    _, side, mean, std = MODELS[key]
    session = _session(path)
    rgb = image.convert("RGB").resize((side, side), Image.Resampling.BILINEAR)
    x = np.asarray(rgb, dtype=np.float32)
    x /= max(float(x.max()), 1e-6)
    x = (x - np.asarray(mean, np.float32)) / np.asarray(std, np.float32)
    x = x.transpose(2, 0, 1)[None]
    pred = session.run(None, {session.get_inputs()[0].name: x})[0][0, 0]
    low, high = float(pred.min()), float(pred.max())
    pred = (pred - low) / max(high - low, 1e-6)
    mask = Image.fromarray((pred * 255).astype(np.uint8), "L")
    return mask.resize(image.size, Image.Resampling.BILINEAR)


def _border_mask(image):
    """Flat-background removal: flood the border colour inward."""

    small = image.convert("RGB")
    small.thumbnail((256, 256), Image.Resampling.BILINEAR)
    w, h = small.size
    pixels = small.load()
    border = [pixels[x, y] for x in range(w) for y in (0, h - 1)]
    border += [pixels[x, y] for y in range(h) for x in (0, w - 1)]
    background = tuple(sorted(c[i] for c in border)[len(border) // 2] for i in range(3))
    distance = ImageChops.difference(small, Image.new("RGB", small.size, background))
    r, g, b = distance.split()
    near = ImageChops.lighter(ImageChops.lighter(r, g), b).point(
        lambda v: 255 if v < 48 else 0
    )
    flood = near.copy()
    step = max(1, min(w, h) // 32)
    seeds = [(x, y) for x in range(0, w, step) for y in (0, h - 1)]
    seeds += [(x, y) for y in range(0, h, step) for x in (0, w - 1)]
    for seed in seeds:
        if flood.getpixel(seed) == 255:
            ImageDraw.floodfill(flood, seed, 128)
    subject = flood.point(lambda v: 0 if v == 128 else 255)
    subject = subject.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    subject = subject.filter(ImageFilter.GaussianBlur(1))
    return subject.resize(image.size, Image.Resampling.BILINEAR)


def _usable(mask):
    """A mask covering almost nothing or almost everything is a failed cut."""

    histogram = mask.histogram()
    total = sum(histogram)
    solid = sum(histogram[128:])
    # A confident cut is mostly 0 or 255; a cloud of half-tones (line art,
    # halftoned photos) would leave a ghostly, see-through figure.
    unsure = sum(histogram[40:216])
    return 0.04 * total < solid < 0.97 * total and unsure < 0.18 * solid


def _key(image):
    digest = hashlib.blake2b(image.tobytes(), digest_size=16)
    digest.update(f"{image.size}{image.mode}{model_name()}".encode())
    return digest.hexdigest()


def cutout(image):
    """``(subject, cut)``: the RGBA subject cropped to its extent, and whether
    the background was actually removed (``False`` keeps the whole image)."""

    image = image.convert("RGBA")
    if max(image.size) > SOURCE_SIDE:
        image = image.copy()
        image.thumbnail((SOURCE_SIDE, SOURCE_SIDE), Image.Resampling.LANCZOS)
    key = _key(image)
    mask = _CACHE.get(key)
    if mask is None:
        try:
            mask = _network_mask(image)
        except Exception:  # noqa: BLE001 - any runtime problem falls back
            mask = None
        if mask is None or not _usable(mask):
            mask = _border_mask(image)
            if not _usable(mask):
                mask = Image.new("L", image.size, 0)
        _CACHE[key] = mask
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    else:
        _CACHE.move_to_end(key)
    if mask.getbbox() is None:
        return image, False
    # Tighten the soft edge a little so no background halo remains.
    alpha = ImageChops.multiply(image.getchannel("A"), mask.point(_EDGE))
    subject = image.copy()
    subject.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 24 else 0).getbbox() or (
        0,
        0,
        *image.size,
    )
    return subject.crop(box), True


_EDGE = [max(0, min(255, round((v - 40) * 255 / 175))) for v in range(256)]
