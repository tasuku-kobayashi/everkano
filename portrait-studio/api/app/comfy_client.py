"""ComfyUI HTTP / WebSocket client.

Endpoints used: GET /system_stats, GET /object_info, GET /models, GET /models/{folder}, POST /prompt,
GET /history/{id}, GET /view, POST /upload/image, POST /interrupt, POST /free, GET /queue, POST /queue, WS /ws.

Progress: the WebSocket (`/ws?clientId=`) delivers `progress` / `executing` / `execution_error` events. If the socket
cannot be opened (or drops), the client falls back to polling `/history/{prompt_id}` (no step progress, still correct).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import websockets
from websockets.exceptions import WebSocketException

logger = logging.getLogger("portrait.comfy")

ProgressCallback = Callable[[int, int], None]


class ComfyError(RuntimeError):
    """ComfyUI returned an error (validation, execution) or is unreachable."""


class ComfyUnavailable(ComfyError):
    """ComfyUI cannot be reached."""


class JobCanceled(Exception):
    """The job was canceled by the user."""


@dataclass(slots=True)
class OutputImage:
    filename: str
    subfolder: str
    type: str
    node_id: str


@dataclass(slots=True)
class PromptResult:
    prompt_id: str
    images: list[OutputImage] = field(default_factory=list)
    duration_ms: int = 0


@dataclass(slots=True)
class SystemStats:
    gpu_name: str | None
    vram_total_mb: int
    vram_free_mb: int
    torch_vram_total_mb: int | None
    torch_vram_free_mb: int | None
    pytorch_version: str | None
    comfy_version: str | None
    raw: dict[str, Any]

    @property
    def cuda_version(self) -> str | None:
        """`2.7.0+cu128` -> `12.8` (ComfyUI does not expose torch.version.cuda directly)."""
        if not self.pytorch_version or "+cu" not in self.pytorch_version:
            return None
        tag = self.pytorch_version.split("+cu", 1)[1]
        digits = "".join(ch for ch in tag if ch.isdigit())
        if len(digits) < 3:
            return None
        return f"{digits[:-1]}.{digits[-1]}"


def _mb(value: Any) -> int:
    try:
        return int(int(value) / (1024 * 1024))
    except (TypeError, ValueError):
        return 0


def _clean_gpu_name(name: str | None) -> str | None:
    # ComfyUI reports e.g. "cuda:0 NVIDIA GeForce RTX 5070 : cudaMallocAsync"
    if not name:
        return None
    cleaned = name
    if cleaned.startswith("cuda:"):
        cleaned = cleaned.split(" ", 1)[1] if " " in cleaned else cleaned
    if " : " in cleaned:
        cleaned = cleaned.split(" : ", 1)[0]
    return cleaned.strip()


class ComfyClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0, poll_interval: float = 0.5) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        # how long to wait for /history to show a finished run (a restarted ComfyUI never will)
        self.history_timeout = 30.0
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def ws_url(self) -> str:
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        return f"{scheme}://{self.base_url.split('://', 1)[1]}/ws"

    # ------------------------------------------------------------------ basic calls
    async def _get_json(self, path: str, **params: Any) -> Any:
        try:
            resp = await self._http.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"ComfyUI に接続できません（{self.base_url}）: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise ComfyError(f"ComfyUI {path} が {resp.status_code} を返しました: {resp.text[:300]}")
        return resp.json()

    async def system_stats(self) -> SystemStats:
        data = await self._get_json("/system_stats")
        devices = data.get("devices") or []
        dev = devices[0] if devices else {}
        system = data.get("system") or {}
        return SystemStats(
            gpu_name=_clean_gpu_name(dev.get("name")),
            vram_total_mb=_mb(dev.get("vram_total")),
            vram_free_mb=_mb(dev.get("vram_free")),
            torch_vram_total_mb=_mb(dev.get("torch_vram_total")) if "torch_vram_total" in dev else None,
            torch_vram_free_mb=_mb(dev.get("torch_vram_free")) if "torch_vram_free" in dev else None,
            pytorch_version=system.get("pytorch_version"),
            comfy_version=system.get("comfyui_version"),
            raw=data,
        )

    async def object_info(self) -> dict[str, Any]:
        data = await self._get_json("/object_info")
        return data if isinstance(data, dict) else {}

    async def list_models(self, folder: str) -> list[str]:
        """GET /models/{folder}. Returns [] when the folder type is unknown to this ComfyUI."""
        try:
            resp = await self._http.get(f"/models/{folder}")
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"ComfyUI に接続できません: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [str(x) for x in data] if isinstance(data, list) else []

    async def list_model_folders(self) -> list[str]:
        try:
            resp = await self._http.get("/models")
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"ComfyUI に接続できません: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [str(x) for x in data] if isinstance(data, list) else []

    async def checkpoints(self) -> list[str]:
        names = await self.list_models("checkpoints")
        if names:
            return names
        # Older ComfyUI without /models: read the combo of CheckpointLoaderSimple
        info = await self._get_json("/object_info/CheckpointLoaderSimple")
        try:
            combo = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
            return [str(x) for x in combo]
        except (KeyError, IndexError, TypeError):
            return []

    async def upload_image(self, data: bytes, filename: str, *, subfolder: str = "refs") -> str:
        """POST /upload/image. Returns the value to put into LoadImage.inputs.image (e.g. `refs/name.png`)."""
        try:
            resp = await self._http.post(
                "/upload/image",
                files={"image": (filename, data, "image/png")},
                data={"subfolder": subfolder, "type": "input", "overwrite": "true"},
            )
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"ComfyUI に接続できません: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise ComfyError(f"参照画像のアップロードに失敗しました（{resp.status_code}）: {resp.text[:300]}")
        body = resp.json()
        name = str(body.get("name", filename))
        sub = str(body.get("subfolder", subfolder) or "")
        return f"{sub}/{name}" if sub else name

    async def queue_prompt(self, prompt: dict[str, Any], client_id: str) -> str:
        try:
            resp = await self._http.post("/prompt", json={"prompt": prompt, "client_id": client_id})
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"ComfyUI に接続できません: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            detail = resp.text[:2000]
            try:
                body = resp.json()
                err = body.get("error", {})
                node_errors = body.get("node_errors", {})
                parts = [str(err.get("message", "")), str(err.get("details", ""))]
                for node_id, ne in node_errors.items():
                    for e in ne.get("errors", []):
                        parts.append(f"node {node_id}: {e.get('message', '')} {e.get('details', '')}".strip())
                detail = " / ".join(p for p in parts if p)
            except ValueError:
                pass
            raise ComfyError(f"ComfyUI がワークフローを拒否しました: {detail}")
        body = resp.json()
        return str(body["prompt_id"])

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        data = await self._get_json(f"/history/{prompt_id}")
        entry = data.get(prompt_id) if isinstance(data, dict) else None
        return entry if isinstance(entry, dict) else None

    async def view(self, image: OutputImage) -> bytes:
        try:
            resp = await self._http.get(
                "/view", params={"filename": image.filename, "subfolder": image.subfolder, "type": image.type}
            )
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"ComfyUI に接続できません: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise ComfyError(f"生成画像の取得に失敗しました（{resp.status_code}）")
        return resp.content

    async def interrupt(self) -> None:
        try:
            await self._http.post("/interrupt")
        except httpx.HTTPError as exc:
            logger.warning("interrupt failed: %s", exc)

    async def delete_queued(self, prompt_id: str) -> None:
        try:
            await self._http.post("/queue", json={"delete": [prompt_id]})
        except httpx.HTTPError as exc:
            logger.warning("queue delete failed: %s", exc)

    async def free(self, *, unload_models: bool = True, free_memory: bool = True) -> None:
        try:
            resp = await self._http.post("/free", json={"unload_models": unload_models, "free_memory": free_memory})
        except httpx.HTTPError as exc:
            raise ComfyUnavailable(f"ComfyUI に接続できません: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise ComfyError(f"/free が {resp.status_code} を返しました")

    # ------------------------------------------------------------------ run a prompt to completion
    @staticmethod
    def _outputs_from_history(entry: dict[str, Any]) -> list[OutputImage]:
        images: list[OutputImage] = []
        for node_id, out in (entry.get("outputs") or {}).items():
            for img in out.get("images") or []:
                if img.get("type", "output") != "output":
                    continue
                images.append(
                    OutputImage(
                        filename=str(img["filename"]),
                        subfolder=str(img.get("subfolder", "")),
                        type=str(img.get("type", "output")),
                        node_id=str(node_id),
                    )
                )
        return images

    @staticmethod
    def _history_error(entry: dict[str, Any]) -> str | None:
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            for msg in status.get("messages") or []:
                if isinstance(msg, list) and len(msg) == 2 and msg[0] == "execution_error":
                    data = msg[1] if isinstance(msg[1], dict) else {}
                    return f"{data.get('node_type', '')}: {data.get('exception_message', 'execution error')}".strip()
            return "ComfyUI の実行がエラーで終了しました"
        return None

    async def run_prompt(
        self,
        prompt: dict[str, Any],
        *,
        on_progress: ProgressCallback | None = None,
        cancel: asyncio.Event | None = None,
        timeout: float = 900.0,
    ) -> PromptResult:
        started = time.monotonic()
        client_id = uuid4().hex
        prompt_id: str | None = None
        try:
            async with websockets.connect(f"{self.ws_url}?clientId={client_id}", open_timeout=5, max_size=None) as ws:
                prompt_id = await self.queue_prompt(prompt, client_id)
                await self._wait_ws(ws, prompt_id, on_progress=on_progress, cancel=cancel, timeout=timeout)
        except (TimeoutError, OSError, WebSocketException) as exc:
            if prompt_id is None:
                logger.info("websocket unavailable (%s); falling back to polling", exc.__class__.__name__)
                prompt_id = await self.queue_prompt(prompt, client_id)
            else:
                logger.info("websocket dropped (%s); polling history for %s", exc.__class__.__name__, prompt_id)
            await self._wait_polling(prompt_id, cancel=cancel, timeout=timeout)
        entry = await self._wait_history_entry(prompt_id, cancel=cancel, timeout=self.history_timeout)
        error = self._history_error(entry)
        if error:
            raise ComfyError(error)
        images = self._outputs_from_history(entry)
        if not images:
            raise ComfyError("ComfyUI は画像を出力しませんでした（SAVE ノードの出力が空です）")
        return PromptResult(prompt_id=prompt_id, images=images, duration_ms=int((time.monotonic() - started) * 1000))

    async def _cancel_prompt(self, prompt_id: str) -> None:
        await self.delete_queued(prompt_id)
        await self.interrupt()

    async def _wait_ws(
        self,
        ws: Any,
        prompt_id: str,
        *,
        on_progress: ProgressCallback | None,
        cancel: asyncio.Event | None,
        timeout: float,
    ) -> None:
        deadline = time.monotonic() + timeout
        last_poll = time.monotonic()
        while True:
            if cancel is not None and cancel.is_set():
                await self._cancel_prompt(prompt_id)
                raise JobCanceled
            if time.monotonic() > deadline:
                await self._cancel_prompt(prompt_id)
                raise ComfyError("生成がタイムアウトしました")
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                # Belt and braces: a finished prompt whose final event was missed is detected via /history.
                if time.monotonic() - last_poll > 5.0:
                    last_poll = time.monotonic()
                    entry = await self.history(prompt_id)
                    if entry and (entry.get("outputs") or self._history_error(entry)):
                        return
                continue
            if isinstance(raw, bytes | bytearray):
                continue  # binary preview frames
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            mtype = msg.get("type")
            data = msg.get("data") or {}
            if data.get("prompt_id") not in (None, prompt_id):
                continue
            if mtype == "progress" and on_progress is not None:
                on_progress(int(data.get("value", 0)), int(data.get("max", 0)))
            elif (mtype == "executing" and data.get("node") is None and data.get("prompt_id") == prompt_id) or (
                mtype == "execution_success" and data.get("prompt_id") == prompt_id
            ):
                return
            elif mtype == "execution_error" and data.get("prompt_id") == prompt_id:
                raise ComfyError(
                    f"{data.get('node_type', '')}: {data.get('exception_message', 'execution error')}".strip()
                )
            elif mtype == "execution_interrupted" and data.get("prompt_id") == prompt_id:
                raise JobCanceled

    async def _wait_polling(self, prompt_id: str, *, cancel: asyncio.Event | None, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                await self._cancel_prompt(prompt_id)
                raise JobCanceled
            if time.monotonic() > deadline:
                await self._cancel_prompt(prompt_id)
                raise ComfyError("生成がタイムアウトしました")
            entry = await self.history(prompt_id)
            if entry and (
                entry.get("outputs") or self._history_error(entry) or (entry.get("status") or {}).get("completed")
            ):
                return
            await asyncio.sleep(self.poll_interval)

    async def _wait_history_entry(
        self, prompt_id: str, *, cancel: asyncio.Event | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        """After the run signalled completion, fetch its history entry (bounded; a restarted ComfyUI loses it)."""
        deadline = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                raise JobCanceled
            entry = await self.history(prompt_id)
            if entry and (
                entry.get("outputs") or self._history_error(entry) or (entry.get("status") or {}).get("completed")
            ):
                return entry
            if time.monotonic() > deadline:
                raise ComfyError("ComfyUI の履歴に結果が現れません（ComfyUI が再起動した可能性）")
            await asyncio.sleep(self.poll_interval)
