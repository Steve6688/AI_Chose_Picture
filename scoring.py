"""Offline JPG quality scoring used by the desktop application."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError


@dataclass
class PhotoScore:
    path: str
    stars: int
    score: float
    sharpness: float
    exposure: float
    contrast: float
    color: float
    composition: float
    resolution: float
    duplicate: bool = False
    favorite: bool = False
    note: str = ""
    width: int = 0
    height: int = 0
    mtime: float = 0.0
    dhash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _bell(value: float, center: float, spread: float) -> float:
    return float(np.exp(-0.5 * ((value - center) / spread) ** 2))


def difference_hash(image: Image.Image, size: int = 8) -> str:
    gray = ImageOps.grayscale(image).resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.int16)
    bits = pixels[:, 1:] > pixels[:, :-1]
    return f"{int(''.join('1' if x else '0' for x in bits.flat), 2):0{size * size // 4}x}"


def hash_distance(left: str, right: str) -> int:
    if not left or not right:
        return 99
    return (int(left, 16) ^ int(right, 16)).bit_count()


def analyze_photo(path: str | Path) -> PhotoScore:
    source = Path(path)
    try:
        with Image.open(source) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"无法读取图片：{source.name}") from exc

    width, height = image.size
    preview = image.copy()
    preview.thumbnail((900, 900), Image.Resampling.LANCZOS)
    rgb = np.asarray(preview, dtype=np.float32) / 255.0
    gray_image = ImageOps.grayscale(preview)
    gray = np.asarray(gray_image, dtype=np.float32) / 255.0

    # Edge energy is a stable, inexpensive proxy for focus/detail.
    edges = np.asarray(gray_image.filter(ImageFilter.FIND_EDGES), dtype=np.float32) / 255.0
    edge_energy = float(np.percentile(edges[3:-3, 3:-3], 90)) if min(edges.shape) > 8 else float(edges.mean())
    sharpness = float(np.clip((edge_energy - 0.055) / 0.22, 0, 1))

    mean_luma = float(gray.mean())
    clipped = float(((gray < 0.025) | (gray > 0.975)).mean())
    exposure = float(np.clip(_bell(mean_luma, 0.50, 0.25) * (1 - clipped * 1.7), 0, 1))

    p5, p95 = np.percentile(gray, [5, 95])
    dynamic_range = float(p95 - p5)
    contrast = float(np.clip(_bell(dynamic_range, 0.62, 0.28), 0, 1))

    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    saturation = float(np.mean((mx - mn) / np.maximum(mx, 0.001)))
    color = float(np.clip(_bell(saturation, 0.32, 0.25), 0, 1))

    # Composition proxy: reward detail near rule-of-thirds intersections and
    # avoid a visually heavy edge. This is intentionally explainable/offline.
    h, w = edges.shape
    yy, xx = np.mgrid[0:h, 0:w]
    sigma = max(min(h, w) * 0.20, 1)
    thirds = np.zeros_like(edges)
    for px in (w / 3, 2 * w / 3):
        for py in (h / 3, 2 * h / 3):
            thirds += np.exp(-((xx - px) ** 2 + (yy - py) ** 2) / (2 * sigma**2))
    thirds /= max(float(thirds.max()), 1e-6)
    weighted = float((edges * thirds).sum() / max(edges.sum(), 1e-6))
    border = np.concatenate((edges[: max(1, h // 12)].flat, edges[-max(1, h // 12) :].flat,
                             edges[:, : max(1, w // 12)].flat, edges[:, -max(1, w // 12) :].flat))
    border_penalty = max(0.0, float(border.mean() - edges.mean()))
    composition = float(np.clip(0.42 + weighted * 1.05 - border_penalty * 1.4, 0, 1))

    megapixels = width * height / 1_000_000
    resolution = float(np.clip(megapixels / 12.0, 0, 1))

    total = 100 * (0.30 * sharpness + 0.20 * exposure + 0.13 * contrast +
                   0.12 * color + 0.18 * composition + 0.07 * resolution)
    stars = 5 if total >= 82 else 4 if total >= 68 else 3 if total >= 54 else 2 if total >= 40 else 1
    weakest = min(
        ((sharpness, "清晰度偏低"), (exposure, "曝光可改善"), (contrast, "层次偏弱"),
         (color, "色彩表现一般"), (composition, "构图可改善")),
        key=lambda item: item[0],
    )[1]
    note = "整体表现优秀" if stars == 5 else "值得保留" if stars == 4 else weakest
    return PhotoScore(
        path=str(source), stars=stars, score=round(total, 1), sharpness=round(sharpness * 100, 1),
        exposure=round(exposure * 100, 1), contrast=round(contrast * 100, 1),
        color=round(color * 100, 1), composition=round(composition * 100, 1),
        resolution=round(resolution * 100, 1), note=note, width=width, height=height,
        mtime=source.stat().st_mtime, dhash=difference_hash(image),
    )


def find_jpgs(folder: str | Path) -> list[Path]:
    root = Path(folder)
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"}),
        key=lambda p: p.name.lower(),
    )


def mark_duplicates(items: list[PhotoScore], distance: int = 5) -> None:
    """Mark the lower scoring image in perceptually similar pairs."""
    ranked = sorted(items, key=lambda x: x.score, reverse=True)
    kept: list[PhotoScore] = []
    for item in ranked:
        item.duplicate = any(hash_distance(item.dhash, other.dhash) <= distance for other in kept)
        if not item.duplicate:
            kept.append(item)

