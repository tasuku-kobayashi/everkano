"""Workflow loading, title-based patching and validation.

ComfyUI re-exports change node ids, so nodes are located by `_meta.title` only. Required titles:
  base : CHECKPOINT LORA POSITIVE NEGATIVE LATENT SAMPLER DECODE SAVE
  face : REF_IMAGE FACE_APPLY FACE_DETAILER (workflows for pulid / faceid / instantid)
Optional titles handled here: UPSCALE + HIRES_SAMPLER (hires-fix), FACE_LOADER* (loaders are left untouched).

Bypass (title -> which upstream output replaces each of its outputs):
  LORA                    -> CHECKPOINT (model, clip)     when no LoRA is configured
  UPSCALE + HIRES_SAMPLER -> SAMPLER (latent)             when upscale == 1.0
  FACE_DETAILER           -> DECODE (image)               when the face detailer is disabled
After bypassing, nodes that no longer feed the SAVE node are dropped so ComfyUI never validates them.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKFLOW_FILES: dict[str, str] = {
    "txt2img": "portrait_txt2img_api.json",
    "pulid": "portrait_pulid_api.json",
    "faceid": "portrait_faceid_api.json",
    "instantid": "portrait_instantid_api.json",
}

REQUIRED_BASE_TITLES: tuple[str, ...] = (
    "CHECKPOINT",
    "LORA",
    "POSITIVE",
    "NEGATIVE",
    "LATENT",
    "SAMPLER",
    "DECODE",
    "SAVE",
)
REQUIRED_FACE_TITLES: tuple[str, ...] = ("REF_IMAGE", "FACE_APPLY", "FACE_DETAILER")

# Which `inputs` keys of FACE_APPLY carry the identity weight, per method (only keys present in the node are set).
FACE_WEIGHT_INPUTS: dict[str, tuple[str, ...]] = {
    "pulid": ("weight",),
    "faceid": ("weight", "weight_faceidv2"),
    "instantid": ("weight",),
}

BYPASS_RULES: dict[str, dict[int, tuple[str, int]]] = {
    "LORA": {0: ("CHECKPOINT", 0), 1: ("CHECKPOINT", 1)},
    "HIRES_SAMPLER": {0: ("SAMPLER", 0)},
    "FACE_DETAILER": {0: ("DECODE", 0)},
}


class WorkflowError(ValueError):
    """The workflow file is unusable; the message says which file and which title/input is wrong."""


@dataclass(slots=True)
class GenerationParams:
    checkpoint: str
    positive: str
    negative: str
    width: int
    height: int
    seed: int
    steps: int | None = None
    cfg: float | None = None
    sampler_name: str | None = None
    scheduler: str | None = None
    upscale: float = 1.0
    hires_denoise: float | None = None
    face_method: str | None = None  # None -> txt2img
    face_weight: float = 0.8
    ref_image: str | None = None  # ComfyUI input path, e.g. refs/<name>.png
    face_detailer: bool = True
    face_detailer_denoise: float = 0.35
    lora: str | None = None
    lora_strength: float = 0.0
    filename_prefix: str = "portrait-studio/img"

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "positive": self.positive,
            "negative": self.negative,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "steps": self.steps,
            "cfg": self.cfg,
            "sampler_name": self.sampler_name,
            "scheduler": self.scheduler,
            "upscale": self.upscale,
            "hires_denoise": self.hires_denoise,
            "face_method": self.face_method,
            "face_weight": self.face_weight,
            "ref_image": self.ref_image,
            "face_detailer": self.face_detailer,
            "face_detailer_denoise": self.face_detailer_denoise,
            "lora": self.lora,
            "lora_strength": self.lora_strength,
        }


@dataclass(slots=True)
class Workflow:
    method: str
    path: Path
    nodes: dict[str, dict[str, Any]]
    titles: dict[str, str] = field(default_factory=dict)  # title -> node id

    @property
    def is_face_workflow(self) -> bool:
        return self.method != "txt2img"

    def node(self, title: str) -> dict[str, Any]:
        try:
            return self.nodes[self.titles[title]]
        except KeyError as exc:
            raise WorkflowError(f"{self.path.name}: ノード title '{title}' がありません") from exc

    def sampler_defaults(self) -> dict[str, Any]:
        inputs = self.node("SAMPLER").get("inputs", {})
        return {
            "steps": inputs.get("steps"),
            "cfg": inputs.get("cfg"),
            "sampler_name": inputs.get("sampler_name"),
            "scheduler": inputs.get("scheduler"),
        }


def _index_titles(path: Path, nodes: dict[str, Any]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or "class_type" not in node:
            raise WorkflowError(f"{path.name}: ノード {node_id} に class_type がありません（API 形式の JSON か確認）")
        title = (node.get("_meta") or {}).get("title")
        if not title:
            raise WorkflowError(f"{path.name}: ノード {node_id}（{node['class_type']}）に _meta.title がありません")
        if title in titles:
            raise WorkflowError(f"{path.name}: title '{title}' が重複しています（{titles[title]} と {node_id}）")
        titles[str(title)] = str(node_id)
    return titles


def load_workflow(path: Path, method: str) -> Workflow:
    if not path.is_file():
        raise WorkflowError(f"ワークフローファイルがありません: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise WorkflowError(f"{path.name}: JSON として読めません: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise WorkflowError(f"{path.name}: API 形式（ノード id をキーにした dict）ではありません")
    nodes: dict[str, dict[str, Any]] = {str(k): v for k, v in data.items()}
    titles = _index_titles(path, nodes)
    required = list(REQUIRED_BASE_TITLES) + (list(REQUIRED_FACE_TITLES) if method != "txt2img" else [])
    missing = [t for t in required if t not in titles]
    if missing:
        raise WorkflowError(
            f"{path.name}: 必須の title がありません: {', '.join(missing)}（各ノードの _meta.title で指定してください）"
        )
    for title, (_, expect) in {
        "UPSCALE": ("HIRES_SAMPLER", "HIRES_SAMPLER"),
        "HIRES_SAMPLER": ("UPSCALE", "UPSCALE"),
    }.items():
        if title in titles and expect not in titles:
            raise WorkflowError(f"{path.name}: {title} があるのに {expect} がありません（hires-fix は両方必要）")
    return Workflow(method=method, path=path, nodes=nodes, titles=titles)


def load_all(workflows_dir: Path) -> dict[str, Workflow]:
    return {method: load_workflow(workflows_dir / filename, method) for method, filename in WORKFLOW_FILES.items()}


# ----------------------------------------------------------------------------- building
def _set_if_present(node: dict[str, Any], key: str, value: Any) -> None:
    if key in node.get("inputs", {}) and value is not None:
        node["inputs"][key] = value


def _rewire(nodes: dict[str, dict[str, Any]], old_id: str, replacement: dict[int, tuple[str, int]]) -> None:
    for node in nodes.values():
        for key, value in list(node.get("inputs", {}).items()):
            if isinstance(value, list) and len(value) == 2 and str(value[0]) == old_id:
                slot = int(value[1])
                if slot not in replacement:
                    raise WorkflowError(f"ノード {old_id} の出力 {slot} をバイパスできません（接続先が未定義）")
                node["inputs"][key] = [replacement[slot][0], replacement[slot][1]]


def _bypass(workflow: Workflow, nodes: dict[str, dict[str, Any]], title: str) -> None:
    if title not in workflow.titles:
        return
    old_id = workflow.titles[title]
    rule = BYPASS_RULES[title]
    replacement = {slot: (workflow.titles[t], out) for slot, (t, out) in rule.items()}
    _rewire(nodes, old_id, replacement)
    nodes.pop(old_id, None)


def _prune_unreachable(nodes: dict[str, dict[str, Any]], root_id: str) -> None:
    keep: set[str] = set()
    stack = [root_id]
    while stack:
        nid = stack.pop()
        if nid in keep or nid not in nodes:
            continue
        keep.add(nid)
        for value in nodes[nid].get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2:
                stack.append(str(value[0]))
    for nid in list(nodes):
        if nid not in keep:
            del nodes[nid]
    for nid, node in nodes.items():
        for key, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) not in nodes:
                raise WorkflowError(f"ノード {nid} の入力 {key} が存在しないノード {value[0]} を参照しています")


def build_prompt(workflow: Workflow, params: GenerationParams) -> dict[str, Any]:
    """Return a ComfyUI prompt (API-format dict) with the parameters applied by title."""
    if workflow.is_face_workflow:
        if params.face_method != workflow.method:
            raise WorkflowError(f"face_method '{params.face_method}' に対して {workflow.path.name} は使えません")
        if not params.ref_image:
            raise WorkflowError("参照画像（ref_image）が指定されていません")
    nodes = copy.deepcopy(workflow.nodes)

    def n(title: str) -> dict[str, Any]:
        return nodes[workflow.titles[title]]

    n("CHECKPOINT")["inputs"]["ckpt_name"] = params.checkpoint
    n("POSITIVE")["inputs"]["text"] = params.positive
    n("NEGATIVE")["inputs"]["text"] = params.negative
    latent = n("LATENT")["inputs"]
    latent["width"] = params.width
    latent["height"] = params.height
    latent["batch_size"] = 1  # never more than 1 on 12 GB

    sampler = n("SAMPLER")
    sampler["inputs"]["seed"] = params.seed
    _set_if_present(sampler, "steps", params.steps)
    _set_if_present(sampler, "cfg", params.cfg)
    _set_if_present(sampler, "sampler_name", params.sampler_name)
    _set_if_present(sampler, "scheduler", params.scheduler)

    if params.lora and params.lora_strength > 0:
        lora = n("LORA")
        lora["inputs"]["lora_name"] = params.lora
        _set_if_present(lora, "strength_model", params.lora_strength)
        _set_if_present(lora, "strength_clip", params.lora_strength)
    else:
        _bypass(workflow, nodes, "LORA")

    if "UPSCALE" in workflow.titles:
        if params.upscale > 1.0:
            n("UPSCALE")["inputs"]["scale_by"] = params.upscale
            hires = n("HIRES_SAMPLER")
            hires["inputs"]["seed"] = params.seed
            _set_if_present(hires, "steps", params.steps)
            _set_if_present(hires, "cfg", params.cfg)
            _set_if_present(hires, "sampler_name", params.sampler_name)
            _set_if_present(hires, "scheduler", params.scheduler)
            _set_if_present(hires, "denoise", params.hires_denoise)
        else:
            _bypass(workflow, nodes, "HIRES_SAMPLER")
    elif params.upscale > 1.0:
        raise WorkflowError(f"{workflow.path.name} には UPSCALE / HIRES_SAMPLER が無いため upscale > 1.0 は使えません")

    if workflow.is_face_workflow:
        n("REF_IMAGE")["inputs"]["image"] = params.ref_image
        apply = n("FACE_APPLY")
        weight_keys = [k for k in FACE_WEIGHT_INPUTS.get(workflow.method, ("weight",)) if k in apply.get("inputs", {})]
        if not weight_keys:
            raise WorkflowError(f"{workflow.path.name}: FACE_APPLY に weight 入力がありません")
        for key in weight_keys:
            apply["inputs"][key] = params.face_weight
        if params.face_detailer:
            fd = n("FACE_DETAILER")
            fd["inputs"]["denoise"] = params.face_detailer_denoise
            _set_if_present(fd, "seed", params.seed)
            _set_if_present(fd, "steps", params.steps)
            _set_if_present(fd, "cfg", params.cfg)
            _set_if_present(fd, "sampler_name", params.sampler_name)
            _set_if_present(fd, "scheduler", params.scheduler)
        else:
            _bypass(workflow, nodes, "FACE_DETAILER")
    elif "FACE_DETAILER" in workflow.titles:
        if params.face_detailer:
            fd = n("FACE_DETAILER")
            fd["inputs"]["denoise"] = params.face_detailer_denoise
            _set_if_present(fd, "seed", params.seed)
        else:
            _bypass(workflow, nodes, "FACE_DETAILER")

    n("SAVE")["inputs"]["filename_prefix"] = params.filename_prefix
    _prune_unreachable(nodes, workflow.titles["SAVE"])
    return nodes


# ----------------------------------------------------------------------------- validation against a live ComfyUI
def validate_against_object_info(workflow: Workflow, object_info: dict[str, Any]) -> list[str]:
    """Compare every node with GET /object_info. Errors are prefixed `error:`; things worth a look with `warn:`.

    A workflow is usable when it has no `error:` entries. Combo values (checkpoint / lora names) are checked
    only as warnings because the API replaces them at generation time.
    """
    issues: list[str] = []
    for node_id, node in workflow.nodes.items():
        title = (node.get("_meta") or {}).get("title", node_id)
        class_type = str(node.get("class_type"))
        spec = object_info.get(class_type)
        if not spec:
            issues.append(
                f"error: {title}: ノードクラス '{class_type}' が ComfyUI に登録されていません（カスタムノード未導入）"
            )
            continue
        inputs_spec = spec.get("input") or {}
        required = inputs_spec.get("required") or {}
        optional = inputs_spec.get("optional") or {}
        given = node.get("inputs") or {}
        for key in required:
            if key not in given:
                issues.append(f"error: {title}: '{class_type}' の必須入力 '{key}' がありません")
        for key, value in given.items():
            if key not in required and key not in optional:
                issues.append(
                    f"warn: {title}: '{class_type}' に入力 '{key}' はありません"
                    "（Impact-Pack 等の更新で名前が変わった可能性）"
                )
                continue
            spec_entry = required.get(key) or optional.get(key)
            if (
                isinstance(spec_entry, list)
                and spec_entry
                and isinstance(spec_entry[0], list)
                and not isinstance(value, list)
            ):
                choices = spec_entry[0]
                if choices and value not in choices and title not in ("CHECKPOINT", "LORA", "REF_IMAGE"):
                    issues.append(
                        f"warn: {title}: '{key}' の値 '{value}' は選択肢にありません"
                        f"（{', '.join(map(str, choices[:6]))}…）"
                    )
    return issues


def workflow_ok(issues: list[str]) -> bool:
    return not any(i.startswith("error:") for i in issues)
