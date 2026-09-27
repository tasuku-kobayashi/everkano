from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.workflow import (
    GenerationParams,
    WorkflowError,
    build_prompt,
    load_all,
    load_workflow,
    validate_against_object_info,
    workflow_ok,
)
from tests.conftest import REPO
from tools.mock_comfy import object_info_from_workflows

WF = REPO / "workflows"


def test_all_shipped_workflows_load_and_have_required_titles() -> None:
    wfs = load_all(WF)
    assert set(wfs) == {"txt2img", "pulid", "faceid", "instantid"}
    for method, wf in wfs.items():
        for title in ("CHECKPOINT", "LORA", "POSITIVE", "NEGATIVE", "LATENT", "SAMPLER", "DECODE", "SAVE"):
            assert title in wf.titles, (method, title)
        if method != "txt2img":
            for title in ("REF_IMAGE", "FACE_APPLY", "FACE_DETAILER"):
                assert title in wf.titles, (method, title)


def test_missing_title_is_a_clear_error(tmp_path: Path) -> None:
    data = json.loads((WF / "portrait_txt2img_api.json").read_text(encoding="utf-8"))
    data["6"]["_meta"]["title"] = "KSAMPLER"
    path = tmp_path / "portrait_txt2img_api.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(WorkflowError, match="SAMPLER"):
        load_workflow(path, "txt2img")


def test_node_without_title_is_rejected(tmp_path: Path) -> None:
    data = json.loads((WF / "portrait_txt2img_api.json").read_text(encoding="utf-8"))
    del data["3"]["_meta"]
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(WorkflowError, match=r"_meta\.title"):
        load_workflow(path, "txt2img")


def test_node_ids_are_not_hardcoded(tmp_path: Path) -> None:
    """Renumbering every node id (as a ComfyUI re-export does) must not change the built prompt's structure."""
    data = json.loads((WF / "portrait_pulid_api.json").read_text(encoding="utf-8"))
    mapping = {old: str(1000 + i) for i, old in enumerate(data)}
    renumbered = {}
    for old, original in data.items():
        node = json.loads(json.dumps(original))
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2:
                node["inputs"][key] = [mapping[str(value[0])], value[1]]
        renumbered[mapping[old]] = node
    path = tmp_path / "portrait_pulid_api.json"
    path.write_text(json.dumps(renumbered), encoding="utf-8")
    wf = load_workflow(path, "pulid")
    params = GenerationParams(
        checkpoint="c",
        positive="p",
        negative="n",
        width=832,
        height=1216,
        seed=3,
        face_method="pulid",
        ref_image="refs/a.png",
        face_weight=0.75,
    )
    prompt = build_prompt(wf, params)
    save = prompt[wf.titles["SAVE"]]
    assert save["inputs"]["images"] == [wf.titles["FACE_DETAILER"], 0]
    assert prompt[wf.titles["FACE_APPLY"]]["inputs"]["weight"] == 0.75
    assert prompt[wf.titles["REF_IMAGE"]]["inputs"]["image"] == "refs/a.png"


def test_bypass_lora_upscale_and_detailer() -> None:
    wf = load_all(WF)["pulid"]
    p = build_prompt(
        wf,
        GenerationParams(
            checkpoint="c",
            positive="p",
            negative="n",
            width=832,
            height=1216,
            seed=1,
            face_method="pulid",
            ref_image="r.png",
            upscale=1.0,
            face_detailer=False,
        ),
    )
    ids = set(p)
    assert wf.titles["LORA"] not in ids and wf.titles["UPSCALE"] not in ids and wf.titles["HIRES_SAMPLER"] not in ids
    assert wf.titles["FACE_DETAILER"] not in ids and wf.titles["FACE_DETECTOR"] not in ids
    assert p[wf.titles["SAVE"]]["inputs"]["images"] == [wf.titles["DECODE"], 0]
    assert p[wf.titles["DECODE"]]["inputs"]["samples"] == [wf.titles["SAMPLER"], 0]
    assert p[wf.titles["FACE_APPLY"]]["inputs"]["model"] == [wf.titles["CHECKPOINT"], 0]
    assert p[wf.titles["LATENT"]]["inputs"]["batch_size"] == 1
    # every link points at a node that still exists
    for node in p.values():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2:
                assert str(value[0]) in p


def test_hires_path_and_lora_kept() -> None:
    wf = load_all(WF)["faceid"]
    p = build_prompt(
        wf,
        GenerationParams(
            checkpoint="c",
            positive="p",
            negative="n",
            width=832,
            height=1216,
            seed=1,
            face_method="faceid",
            ref_image="r.png",
            upscale=1.3,
            lora="x.safetensors",
            lora_strength=0.7,
            hires_denoise=0.4,
        ),
    )
    assert p[wf.titles["UPSCALE"]]["inputs"]["scale_by"] == 1.3
    assert p[wf.titles["HIRES_SAMPLER"]]["inputs"]["denoise"] == 0.4
    assert p[wf.titles["LORA"]]["inputs"]["lora_name"] == "x.safetensors"
    assert p[wf.titles["LORA"]]["inputs"]["strength_model"] == 0.7
    assert p[wf.titles["FACE_APPLY"]]["inputs"]["weight_faceidv2"] == 0.8


def test_face_workflow_requires_ref_and_matching_method() -> None:
    wf = load_all(WF)["pulid"]
    with pytest.raises(WorkflowError, match="ref_image"):
        build_prompt(
            wf,
            GenerationParams(
                checkpoint="c", positive="p", negative="n", width=832, height=1216, seed=1, face_method="pulid"
            ),
        )
    with pytest.raises(WorkflowError, match="faceid"):
        build_prompt(
            wf,
            GenerationParams(
                checkpoint="c",
                positive="p",
                negative="n",
                width=832,
                height=1216,
                seed=1,
                face_method="faceid",
                ref_image="r.png",
            ),
        )


def test_validate_against_object_info_reports_missing_class_and_inputs() -> None:
    wf = load_all(WF)["instantid"]
    info = object_info_from_workflows()
    assert workflow_ok(validate_against_object_info(wf, info))
    del info["ApplyInstantID"]
    issues = validate_against_object_info(wf, info)
    assert any(i.startswith("error:") and "ApplyInstantID" in i for i in issues)
    assert not workflow_ok(issues)
    info = object_info_from_workflows()
    info["FaceDetailer"]["input"]["required"]["new_required_input"] = ["INT", {}]
    del info["FaceDetailer"]["input"]["required"]["cycle"]
    issues = validate_against_object_info(wf, info)
    assert any("new_required_input" in i and i.startswith("error:") for i in issues)
    assert any("cycle" in i and i.startswith("warn:") for i in issues)
