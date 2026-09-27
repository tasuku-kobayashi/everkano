"""Test helpers: synthetic images understood by app.face.MockFaceEngine and tools.mock_comfy."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageFilter

from tools.mock_comfy import render_face_image


def face_png(
    identity: int,
    *,
    seed: int = 1,
    width: int = 640,
    height: int = 800,
    yaw_deg: float = 0.0,
    face_scale: float = 0.45,
    blur: bool = False,
) -> bytes:
    yaw_code = round((yaw_deg + 60.0) / 120.0 * 255.0)
    data = render_face_image(width, height, identity=identity, seed=seed, yaw_code=yaw_code, face_scale=face_scale)
    if blur:
        im = Image.open(io.BytesIO(data)).filter(ImageFilter.GaussianBlur(6))
        arr = np.asarray(im.convert("RGB")).copy()
        # keep the marker channels exact so the face is still detected, but flat (no texture -> low sharpness)
        mask = (arr[..., 0] > 200) & (arr[..., 2] > 200)
        arr[mask, 0] = 255
        arr[mask, 2] = 255
        arr[mask, 1] = identity
        arr[0, 0] = (255, yaw_code, 255)
        buf = io.BytesIO()
        Image.fromarray(arr, "RGB").save(buf, format="PNG")
        return buf.getvalue()
    return data


def landscape_png(width: int = 640, height: int = 400) -> bytes:
    rng = np.random.default_rng(7)
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[..., 0] = np.linspace(30, 120, width, dtype=np.uint8)[None, :]
    arr[..., 1] = np.linspace(90, 180, height, dtype=np.uint8)[:, None]
    arr[..., 2] = 60
    arr = np.clip(arr.astype(np.int16) + rng.integers(-10, 10, size=arr.shape), 0, 200).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG")
    return buf.getvalue()


def two_faces_png(identity_a: int, identity_b: int) -> bytes:
    a = np.asarray(Image.open(io.BytesIO(face_png(identity_a, width=400, height=500))).convert("RGB"))
    b = np.asarray(Image.open(io.BytesIO(face_png(identity_b, width=400, height=500))).convert("RGB"))
    arr = np.concatenate([a, b], axis=1)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG")
    return buf.getvalue()
