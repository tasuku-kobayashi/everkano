"""Mock ComfyUI for tests and UI development on a machine without a GPU.

    uv run python -m tools.mock_comfy --port 8188        # then COMFY_URL=http://127.0.0.1:8188

Implements the subset of the ComfyUI HTTP / WebSocket API the backend uses. "Generated" images are synthetic
PNGs that carry a face marker understood by `app.face.MockFaceEngine` (FACE_ENGINE=mock):
  - txt2img prompts: a face whose identity derives from the seed (same seed -> same identity)
  - face workflows: the identity of the REF_IMAGE (LoadImage) is copied when the weight is >= 0.8,
    otherwise a different identity is drawn (so verification shows lower similarity for low weights).
Nothing here resembles a real GPU: system_stats reports a "Mock GPU" and pytorch "0.0.0+mock".
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

logger = logging.getLogger("mock-comfy")

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "workflows"
CHECKPOINTS = ["mock_photoreal_xl_v1.safetensors", "mock_photoreal_xl_v2.safetensors"]


# ----------------------------------------------------------------------------- synthetic images
def render_face_image(
    width: int, height: int, *, identity: int, seed: int, yaw_code: int = 128, face_scale: float = 0.45
) -> bytes:
    """PNG with a marker face: pixels (R=255, G=identity±noise, B=255). Top-left pixel encodes yaw."""
    rng = np.random.default_rng(seed)
    base = rng.integers(60, 200, size=3)
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[..., 0] = int(base[0])
    img[..., 1] = int(base[1])
    img[..., 2] = int(base[2])
    # texture so the background is not flat
    noise = rng.integers(-12, 12, size=(height, width, 1)).astype(np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 254).astype(np.uint8)
    fw = max(8, int(width * face_scale))
    y1 = int(height * 0.18)
    fh = max(8, min(int(fw * 1.25), height - y1 - 1))
    x1 = (width - fw) // 2
    # +-24 texture keeps the median (= identity) stable while giving the crop a real Laplacian variance
    g = np.clip(identity + rng.integers(-24, 25, size=(fh, fw)), 1, 249).astype(np.uint8)
    img[y1 : y1 + fh, x1 : x1 + fw, 0] = 255
    img[y1 : y1 + fh, x1 : x1 + fw, 1] = g
    img[y1 : y1 + fh, x1 : x1 + fw, 2] = 255
    img[0, 0] = (255, yaw_code, 255)
    buf = io.BytesIO()
    Image.fromarray(img, "RGB").save(buf, format="PNG", compress_level=1)  # noisy images: fast encode matters
    return buf.getvalue()


def identity_of_image(data: bytes) -> int | None:
    arr = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    mask = (arr[..., 0] == 255) & (arr[..., 2] == 255) & (arr[..., 1] < 250)
    if mask.sum() < 64:
        return None
    return int(np.median(arr[..., 1][mask]))


# ----------------------------------------------------------------------------- object_info from workflows
def object_info_from_workflows(workflows_dir: Path = WORKFLOWS_DIR) -> dict[str, Any]:
    """Build a plausible /object_info for every class used by the shipped workflows."""
    info: dict[str, Any] = {}
    for path in workflows_dir.glob("*_api.json"):
        for node in json.loads(path.read_text(encoding="utf-8")).values():
            cls = node["class_type"]
            spec = info.setdefault(cls, {"input": {"required": {}, "optional": {}}, "output": [], "name": cls})
            for key, value in node.get("inputs", {}).items():
                if key in spec["input"]["required"]:
                    continue
                if isinstance(value, list):
                    spec["input"]["required"][key] = ["MODEL"]
                elif cls == "CheckpointLoaderSimple" and key == "ckpt_name":
                    spec["input"]["required"][key] = [list(CHECKPOINTS), {}]
                elif isinstance(value, bool):
                    spec["input"]["required"][key] = ["BOOLEAN", {"default": value}]
                elif isinstance(value, int):
                    spec["input"]["required"][key] = ["INT", {"default": value}]
                elif isinstance(value, float):
                    spec["input"]["required"][key] = ["FLOAT", {"default": value}]
                else:
                    spec["input"]["required"][key] = ["STRING", {"default": value}]
    # optional inputs the real nodes have (so extra keys do not warn)
    for cls in ("FaceDetailer",):
        if cls in info:
            info[cls]["input"]["optional"] = {"sam_model_opt": ["SAM_MODEL"], "segm_detector_opt": ["SEGM_DETECTOR"]}
    return info


# ----------------------------------------------------------------------------- server state
@dataclass
class MockState:
    input_dir: Path
    output_dir: Path
    vram_total_mb: int = 12282
    vram_free_mb: int = 11000
    step_delay: float = 0.02
    steps: int = 4
    fail_next: str | None = None  # error message injected into the next execution
    drop_history: bool = False  # simulate a ComfyUI restart: /history never shows finished runs
    missing_classes: set[str] = field(default_factory=set)
    history: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    running: str | None = None
    interrupt: bool = False
    prompt_count: int = 0
    sockets: set[WebSocket] = field(default_factory=set)
    uploads: dict[str, bytes] = field(default_factory=dict)


def _find_by_title(prompt: dict[str, Any], title: str) -> dict[str, Any] | None:
    for node in prompt.values():
        if (node.get("_meta") or {}).get("title") == title:
            return node
    return None


def _find_by_class(prompt: dict[str, Any], cls: str) -> dict[str, Any] | None:
    for node in prompt.values():
        if node.get("class_type") == cls:
            return node
    return None


def create_mock_app(state: MockState) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        task = asyncio.create_task(worker())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="mock ComfyUI", lifespan=lifespan)
    object_info = object_info_from_workflows()

    async def broadcast(msg: dict[str, Any]) -> None:
        dead = []
        for ws in list(state.sockets):
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                dead.append(ws)
        for ws in dead:
            state.sockets.discard(ws)

    async def execute(prompt_id: str, prompt: dict[str, Any]) -> None:
        state.running = prompt_id
        state.interrupt = False
        try:
            if state.fail_next:
                message = state.fail_next
                state.fail_next = None
                state.history[prompt_id] = {
                    "prompt": [0, prompt_id, prompt, {}, []],
                    "outputs": {},
                    "status": {
                        "status_str": "error",
                        "completed": False,
                        "messages": [
                            [
                                "execution_error",
                                {"prompt_id": prompt_id, "node_type": "MockNode", "exception_message": message},
                            ]
                        ],
                    },
                }
                await broadcast(
                    {
                        "type": "execution_error",
                        "data": {"prompt_id": prompt_id, "node_type": "MockNode", "exception_message": message},
                    }
                )
                return
            for step in range(1, state.steps + 1):
                if state.interrupt:
                    await broadcast({"type": "execution_interrupted", "data": {"prompt_id": prompt_id}})
                    state.history[prompt_id] = {
                        "prompt": [],
                        "outputs": {},
                        "status": {
                            "status_str": "error",
                            "completed": False,
                            "messages": [["execution_interrupted", {"prompt_id": prompt_id}]],
                        },
                    }
                    return
                await asyncio.sleep(state.step_delay)
                await broadcast(
                    {
                        "type": "progress",
                        "data": {"value": step, "max": state.steps, "prompt_id": prompt_id, "node": "6"},
                    }
                )
            latent = _find_by_title(prompt, "LATENT") or {"inputs": {"width": 832, "height": 1216}}
            width = int(latent["inputs"]["width"])
            height = int(latent["inputs"]["height"])
            upscale = _find_by_title(prompt, "UPSCALE")
            if upscale:
                width = int(width * float(upscale["inputs"].get("scale_by", 1.0)))
                height = int(height * float(upscale["inputs"].get("scale_by", 1.0)))
            sampler = _find_by_title(prompt, "SAMPLER") or {"inputs": {"seed": 0}}
            seed = int(sampler["inputs"].get("seed", 0))
            ref = _find_by_title(prompt, "REF_IMAGE")
            apply = _find_by_title(prompt, "FACE_APPLY")
            identity: int
            if ref is not None:
                ref_name = str(ref["inputs"]["image"])
                data = state.uploads.get(ref_name)
                if data is None:
                    path = state.input_dir / ref_name
                    data = path.read_bytes() if path.is_file() else b""
                ref_identity = identity_of_image(data) if data else None
                weight = float((apply or {}).get("inputs", {}).get("weight", 0.8))
                if ref_identity is None:
                    identity = 1 + seed % 200
                elif weight >= 0.8:
                    identity = ref_identity
                else:
                    identity = (ref_identity + 97) % 200 + 1  # a different person for low weights
            else:
                identity = 1 + seed % 200
            png = render_face_image(width, height, identity=identity, seed=seed)
            save = _find_by_title(prompt, "SAVE") or {"inputs": {"filename_prefix": "mock"}}
            prefix = str(save["inputs"].get("filename_prefix", "mock")).replace("/", "_")
            filename = f"{prefix}_{state.prompt_count:05d}_.png"
            (state.output_dir / filename).write_bytes(png)
            save_id = next((nid for nid, n in prompt.items() if n is save), "40")
            state.history[prompt_id] = {
                "prompt": [0, prompt_id, prompt, {}, [save_id]],
                "outputs": {save_id: {"images": [{"filename": filename, "subfolder": "", "type": "output"}]}},
                "status": {"status_str": "success", "completed": True, "messages": []},
            }
            await broadcast({"type": "executing", "data": {"node": None, "prompt_id": prompt_id}})
            await broadcast({"type": "execution_success", "data": {"prompt_id": prompt_id}})
        finally:
            state.running = None

    async def worker() -> None:
        while True:
            if state.pending:
                prompt_id, prompt = state.pending.pop(0)
                await execute(prompt_id, prompt)
            else:
                await asyncio.sleep(0.01)

    @app.get("/system_stats")
    async def system_stats() -> dict[str, Any]:
        mb = 1024 * 1024
        return {
            "system": {
                "os": "mock",
                "comfyui_version": "mock-0.0",
                "pytorch_version": "0.0.0+mock",
                "python_version": "3.x",
            },
            "devices": [
                {
                    "name": "cuda:0 Mock GPU (ComfyUI mock, no real GPU) : mock",
                    "type": "mock",
                    "index": 0,
                    "vram_total": state.vram_total_mb * mb,
                    "vram_free": state.vram_free_mb * mb,
                    "torch_vram_total": state.vram_total_mb * mb,
                    "torch_vram_free": state.vram_free_mb * mb,
                }
            ],
        }

    @app.get("/object_info")
    async def get_object_info() -> dict[str, Any]:
        return {k: v for k, v in object_info.items() if k not in state.missing_classes}

    @app.get("/object_info/{cls}")
    async def get_object_info_one(cls: str) -> dict[str, Any]:
        return {cls: object_info[cls]} if cls in object_info else {}

    @app.get("/models")
    async def models() -> list[str]:
        return [
            "checkpoints",
            "loras",
            "controlnet",
            "pulid",
            "ipadapter",
            "insightface",
            "instantid",
            "clip_vision",
            "vae",
        ]

    @app.get("/models/{folder}")
    async def models_folder(folder: str) -> list[str]:
        if folder == "checkpoints":
            return list(CHECKPOINTS)
        if folder == "loras":
            return ["mock_style_lora.safetensors"]
        if folder == "pulid":
            return ["ip-adapter_pulid_sdxl_fp16.safetensors"]
        return []

    @app.post("/upload/image")
    async def upload_image(
        image: UploadFile = File(...),
        subfolder: str = Form(""),
        type: str = Form("input"),
        overwrite: str = Form("false"),
    ) -> dict[str, Any]:
        data = await image.read()
        name = image.filename or f"{uuid4().hex}.png"
        target_dir = state.input_dir / subfolder if subfolder else state.input_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_bytes(data)
        key = f"{subfolder}/{name}" if subfolder else name
        state.uploads[key] = data
        return {"name": name, "subfolder": subfolder, "type": type}

    @app.post("/prompt")
    async def post_prompt(body: dict[str, Any]) -> JSONResponse:
        prompt = body.get("prompt")
        if not isinstance(prompt, dict) or not prompt:
            raise HTTPException(status_code=400, detail={"error": {"message": "no prompt"}})
        node_errors: dict[str, Any] = {}
        for nid, node in prompt.items():
            cls = node.get("class_type")
            if cls not in object_info or cls in state.missing_classes:
                node_errors[nid] = {"errors": [{"message": f"Node type '{cls}' not registered", "details": ""}]}
            if cls == "LoadImage":
                name = str(node["inputs"].get("image", ""))
                if name not in state.uploads and not (state.input_dir / name).is_file():
                    node_errors[nid] = {"errors": [{"message": "Value not in list: image", "details": name}]}
            if cls == "CheckpointLoaderSimple" and node["inputs"].get("ckpt_name") not in CHECKPOINTS:
                node_errors[nid] = {
                    "errors": [
                        {"message": "Value not in list: ckpt_name", "details": str(node["inputs"].get("ckpt_name"))}
                    ]
                }
            if cls == "EmptyLatentImage" and int(node["inputs"].get("batch_size", 1)) != 1:
                node_errors[nid] = {"errors": [{"message": "mock refuses batch_size > 1", "details": ""}]}
        if node_errors:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "type": "prompt_outputs_failed_validation",
                        "message": "Prompt outputs failed validation",
                        "details": "",
                    },
                    "node_errors": node_errors,
                },
            )
        state.prompt_count += 1
        prompt_id = uuid4().hex
        state.pending.append((prompt_id, prompt))
        return JSONResponse({"prompt_id": prompt_id, "number": state.prompt_count, "node_errors": {}})

    @app.get("/history/{prompt_id}")
    async def history(prompt_id: str) -> dict[str, Any]:
        entry = None if state.drop_history else state.history.get(prompt_id)
        return {prompt_id: entry} if entry else {}

    @app.get("/view")
    async def view(filename: str, subfolder: str = "", type: str = "output") -> FileResponse:
        base = state.output_dir if type == "output" else state.input_dir
        path = base / subfolder / filename if subfolder else base / filename
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(str(path), media_type="image/png")

    @app.post("/interrupt")
    async def interrupt() -> dict[str, Any]:
        state.interrupt = True
        return {}

    @app.post("/free")
    async def free(_body: dict[str, Any] | None = None) -> dict[str, Any]:
        state.vram_free_mb = state.vram_total_mb - 300
        return {}

    @app.get("/queue")
    async def queue() -> dict[str, Any]:
        return {
            "queue_running": [[0, state.running, {}, {}, []]] if state.running else [],
            "queue_pending": [[i + 1, pid, {}, {}, []] for i, (pid, _) in enumerate(state.pending)],
        }

    @app.post("/queue")
    async def queue_delete(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("clear"):
            state.pending.clear()
        for pid in body.get("delete", []):
            state.pending = [(p, w) for p, w in state.pending if p != pid]
        return {}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        state.sockets.add(websocket)
        try:
            await websocket.send_text(
                json.dumps(
                    {"type": "status", "data": {"status": {"exec_info": {"queue_remaining": len(state.pending)}}}}
                )
            )
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            state.sockets.discard(websocket)

    return app


# ----------------------------------------------------------------------------- embedding in tests
class MockComfyServer:
    """Runs the mock in a background thread on a free localhost port."""

    def __init__(self, root: Path, *, port: int | None = None) -> None:
        self.root = root
        (root / "input").mkdir(parents=True, exist_ok=True)
        (root / "output").mkdir(parents=True, exist_ok=True)
        self.state = MockState(input_dir=root / "input", output_dir=root / "output")
        self.port = port or _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            create_mock_app(self.state), host="127.0.0.1", port=self.port, log_level="warning", ws="auto"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="mock-comfy", daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("mock ComfyUI did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock ComfyUI (no GPU) for UI development and tests")
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--root", type=Path, default=Path("/tmp/mock-comfy"))
    parser.add_argument("--step-delay", type=float, default=0.3, help="seconds per fake sampling step")
    parser.add_argument("--steps", type=int, default=12)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    server = MockComfyServer(args.root, port=args.port)
    server.state.step_delay = args.step_delay
    server.state.steps = args.steps
    logger.info("mock ComfyUI on %s (root %s)", server.url, args.root)
    server.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
