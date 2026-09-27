"""Write synthetic face PNGs for the web E2E tests: `python -m tools.make_e2e_fixtures <dir>`."""

from __future__ import annotations

import sys
from pathlib import Path

from tools.mock_comfy import render_face_image


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-fixtures")
    out.mkdir(parents=True, exist_ok=True)
    specs = {
        "face_a_front.png": {"identity": 120, "seed": 1, "yaw_code": 128},
        "face_a_side.png": {"identity": 120, "seed": 2, "yaw_code": 200},  # ~+34 deg yaw -> not frontal
        "face_b_front.png": {"identity": 40, "seed": 3, "yaw_code": 128},
        "landscape.png": None,
    }
    for name, spec in specs.items():
        if spec is None:
            import numpy as np  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415

            arr = np.zeros((400, 640, 3), dtype=np.uint8)
            arr[..., 0] = np.linspace(30, 120, 640, dtype=np.uint8)[None, :]
            arr[..., 1] = np.linspace(90, 180, 400, dtype=np.uint8)[:, None]
            arr[..., 2] = 60
            Image.fromarray(arr, "RGB").save(out / name)
        else:
            (out / name).write_bytes(render_face_image(640, 800, **spec))
    print(f"fixtures written to {out}")


if __name__ == "__main__":
    main()
