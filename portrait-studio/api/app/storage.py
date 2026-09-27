"""File storage under DATA_DIR: images/<kind>/<id>.png (+ .json sidecar), thumbs/<id>.webp, refs/<char>/v<n>/."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


class Storage:
    def __init__(self, data_dir: Path, thumbnail_size: int = 384) -> None:
        self.data_dir = data_dir
        self.thumbnail_size = thumbnail_size
        for sub in ("images", "thumbs", "refs", "uploads", "logs", "tmp"):
            (data_dir / sub).mkdir(parents=True, exist_ok=True)

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
        """Write bytes as PNG (re-encoding non-PNG uploads) and return (width, height)."""
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            if (im.format or "").upper() == "PNG":
                dest.write_bytes(data)
            else:
                im.convert("RGB").save(dest, format="PNG")
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
