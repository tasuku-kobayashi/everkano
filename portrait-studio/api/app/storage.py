"""File storage under DATA_DIR: images/<kind>/<id>.png (+ .json sidecar), thumbs/<id>.webp, refs/<char>/v<n>/."""

from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image

# Uploads / outputs larger than this are refused before decoding (decompression-bomb guard). 24 MP is far above the
# 1024x1536 maximum the generator produces and well below Pillow's own warning threshold.
MAX_IMAGE_PIXELS = 24_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ImageTooLargeError(ValueError):
    pass


class Storage:
    def __init__(self, data_dir: Path, thumbnail_size: int = 384) -> None:
        self.data_dir = data_dir
        self.thumbnail_size = thumbnail_size
        for sub in ("images", "thumbs", "refs", "uploads", "logs", "tmp"):
            (data_dir / sub).mkdir(parents=True, exist_ok=True)
        self.sweep_tmp()

    def sweep_tmp(self, max_age_seconds: float = 3600.0) -> int:
        """Remove leftovers (ZIP downloads whose client went away) older than max_age_seconds."""
        removed = 0
        cutoff = time.time() - max_age_seconds
        for path in (self.data_dir / "tmp").glob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def image_path(self, kind: str, image_id: str) -> Path:
        directory = self.data_dir / "images" / kind
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{image_id}.png"

    def thumb_path(self, image_id: str) -> Path:
        return self.data_dir / "thumbs" / f"{image_id}.webp"

    def ref_dir(self, character_id: str, version: int) -> Path:
        directory = self.data_dir / "refs" / character_id / f"v{version}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def db_path(self) -> Path:
        return self.data_dir / "portrait_studio.sqlite3"

    @staticmethod
    def save_png(data: bytes, dest: Path) -> tuple[int, int]:
        """Validate (pixel budget from the header, then a full decode so truncated / corrupt data is rejected) and
        store as PNG. A PNG is written byte-for-byte (re-encoding an 832×1216 output costs ~300 ms per image, which
        the generation loop cannot afford); anything else (JPEG / WebP uploads) is converted. Returns (w, h)."""
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ImageTooLargeError(
                    f"画像が大きすぎます（{width}×{height}。上限 {MAX_IMAGE_PIXELS // 1_000_000} メガピクセル）"
                )
            is_png = im.format == "PNG"
            im.load()
            if not is_png:
                im.convert("RGB").save(dest, format="PNG", compress_level=1)
        if is_png:
            dest.write_bytes(data)
        return width, height

    def make_thumbnail(self, src: Path, dest: Path) -> None:
        with Image.open(src) as raw:
            im = raw.convert("RGB")
            im.thumbnail((self.thumbnail_size, self.thumbnail_size))
            im.save(dest, format="WEBP", quality=82, method=4)

    @staticmethod
    def remove(path: Path | str | None) -> bool:
        if not path:
            return False
        p = Path(path)
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return False
