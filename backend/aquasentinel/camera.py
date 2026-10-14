"""Simulated camera frames for the demo.

The boat's camera is not connected to the simulator, so frames are drawn procedurally to match
the active pollution scenario. They are stand-ins for testing the Visual Inspector agent and are
always labelled as simulated in the dashboard. `labels` is the ground truth of what was drawn.
"""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 512, 320
HORIZON = 84
VISUAL_LABELS = ["algal_bloom", "foam", "oil_sheen", "floating_waste", "outfall_pipe"]

# Water tint at full strength (R, G, B) per scenario
TINT = {"sewage": (112, 104, 88), "farm_runoff": (118, 122, 70), "industrial": (64, 62, 78), "algal_bloom": (70, 132, 52)}


def render(kind: str | None, strength: float, near_source: bool, seed: int) -> tuple[bytes, list[str]]:
    """Draw one frame. Returns (JPEG bytes, labels present in the frame)."""
    rng = np.random.default_rng(seed)
    s = float(max(0.0, min(1.0, strength)))
    labels: list[str] = []

    # ---- sky, far shore and water with ripples
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    img = np.zeros((H, W, 3), np.float32)
    sky = np.array([186, 208, 226], np.float32)
    img[:HORIZON] = sky - (yy[:HORIZON, :, None] / HORIZON) * np.array([18, 12, 6], np.float32)
    depth = (yy[HORIZON:] - HORIZON) / (H - HORIZON)
    water = np.array([46, 104, 128], np.float32)[None, None] + depth[..., None] * np.array([-14, -30, -34], np.float32)
    if kind in TINT and s > 0.05:
        water = water * (1 - 0.85 * s) + np.array(TINT[kind], np.float32) * (0.85 * s)
    ripple = (np.sin(xx[HORIZON:] * 0.045 + yy[HORIZON:] * 0.6 + rng.uniform(0, 6)) * 5 +
              np.sin(xx[HORIZON:] * 0.11 - yy[HORIZON:] * 0.31) * 3) * (0.4 + depth)
    img[HORIZON:] = water + ripple[..., None] + rng.normal(0, 2.2, (H - HORIZON, W, 1))
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im, "RGBA")
    # far shore: tree line and bank
    for x in range(0, W, 6):
        h = 14 + 7 * math.sin(x * 0.05 + seed) + rng.uniform(0, 5)
        d.rectangle([x, HORIZON - h, x + 6, HORIZON], fill=(44, 78, 50, 255))
    d.rectangle([0, HORIZON - 4, W, HORIZON + 2], fill=(104, 92, 70, 255))

    def blobs(n, colour, rmin, rmax, band=(HORIZON + 14, H - 6), squash=0.35):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for _ in range(n):
            cx, cy = rng.uniform(0, W), rng.uniform(*band)
            scale = 0.35 + 0.65 * (cy - HORIZON) / (H - HORIZON)          # smaller toward the horizon
            r = rng.uniform(rmin, rmax) * scale
            ld.ellipse([cx - r, cy - r * squash, cx + r, cy + r * squash], fill=colour)
        im.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1.6)))

    im = im.convert("RGBA")
    if kind == "algal_bloom" and s > 0.15:
        blobs(int(30 + 150 * s), (88, 168, 44, 210), 14, 46)
        blobs(int(20 + 60 * s), (146, 196, 62, 190), 6, 18)
        labels.append("algal_bloom")
    if kind == "farm_runoff" and s > 0.35:
        blobs(int(10 + 55 * s), (104, 158, 52, 150), 12, 34)
        labels.append("algal_bloom")
    if kind == "sewage" and s > 0.2:
        blobs(int(12 + 70 * s), (236, 234, 224, 225), 4, 13, squash=0.5)       # foam
        labels.append("foam")
        if s > 0.3:
            d = ImageDraw.Draw(im, "RGBA")
            for _ in range(int(3 + 9 * s)):                                     # floating plastic and litter
                cx, cy = rng.uniform(10, W - 10), rng.uniform(HORIZON + 30, H - 10)
                w, h = rng.uniform(8, 22), rng.uniform(4, 9)
                col = [(214, 60, 52), (240, 240, 240), (52, 96, 190), (232, 196, 60), (40, 40, 40)][int(rng.integers(5))]
                d.rectangle([cx, cy, cx + w, cy + h], fill=(*col, 255))
            labels.append("floating_waste")
    if kind == "industrial" and s > 0.2:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for _ in range(int(2 + 5 * s)):                                         # iridescent oil sheen
            cx, cy = rng.uniform(40, W - 40), rng.uniform(HORIZON + 40, H - 24)
            for i, col in enumerate([(198, 72, 190), (72, 122, 222), (64, 200, 190), (226, 214, 84), (226, 132, 72)]):
                r = (64 - 11 * i) * (0.5 + 0.5 * (cy - HORIZON) / (H - HORIZON))
                ld.ellipse([cx - r, cy - r * 0.32, cx + r, cy + r * 0.32], fill=(*col, 118))
        im.alpha_composite(layer.filter(ImageFilter.GaussianBlur(2.2)))
        labels.append("oil_sheen")
    if near_source and kind in ("sewage", "industrial"):
        d = ImageDraw.Draw(im, "RGBA")
        px = int(rng.uniform(150, 360))
        d.rectangle([px - 34, HORIZON - 30, px + 34, HORIZON + 6], fill=(150, 148, 142, 255))        # headwall
        d.ellipse([px - 17, HORIZON - 24, px + 17, HORIZON + 6], fill=(34, 32, 30, 255), outline=(96, 94, 90, 255), width=3)
        flow = (92, 82, 64, 200) if kind == "sewage" else (58, 50, 86, 200)
        d.polygon([(px - 9, HORIZON), (px + 9, HORIZON), (px + 30, HORIZON + 46), (px - 30, HORIZON + 46)], fill=flow)
        labels.append("outfall_pipe")

    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=84)
    return buf.getvalue(), labels
