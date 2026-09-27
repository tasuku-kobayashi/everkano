from __future__ import annotations

from pathlib import Path

from app.vram import VramTable, assess

TABLE = VramTable.load(Path(__file__).parent / "fixtures" / "vram_table.test.json")


def test_exact_measured() -> None:
    e = TABLE.estimate("pulid", 832, 1216, 1.3, True)
    assert e.basis == "measured" and e.peak_mb == 9800


def test_bounded_by_costlier_measurement() -> None:
    e = TABLE.estimate("pulid", 768, 1024, 1.0, True)  # fewer pixels than 832x1216
    assert e.basis == "bounded" and e.peak_mb == 8600
    e = TABLE.estimate("txt2img", 832, 1216, 1.0, True)  # txt2img+detailer bounded by pulid+detailer
    assert e.basis == "bounded" and e.peak_mb == 8600
    e = TABLE.estimate("faceid", 832, 1216, 1.0, False)  # no-detailer bounded by with-detailer row
    assert e.basis == "bounded" and e.peak_mb == 9500


def test_unknown_when_nothing_bounds_it() -> None:
    assert TABLE.estimate("instantid", 1024, 1536, 1.0, True).basis == "unknown"
    assert TABLE.estimate("pulid", 2048, 2048, 1.0, True).basis == "unknown"
    empty = VramTable.load(Path("/nonexistent/vram.json"))
    assert empty.estimate("pulid", 832, 1216, 1.0, True).basis == "unknown"


def test_assess_levels() -> None:
    common = {"method": "pulid", "width": 832, "height": 1216, "upscale": 1.0, "face_detailer": True, "margin_mb": 512}
    a = assess(
        TABLE.estimate("pulid", 832, 1216, 1.0, True), free_mb=11000, total_mb=12282, allow_unmeasured=False, **common
    )
    assert a.risk == "low" and a.would_reject is False and a.basis == "measured"
    a = assess(
        TABLE.estimate("pulid", 832, 1216, 1.0, True), free_mb=5000, total_mb=12282, allow_unmeasured=False, **common
    )
    assert a.risk == "medium" and a.would_reject is False and "空き VRAM" in a.advice
    a = assess(
        TABLE.estimate("pulid", 1536, 1536, 1.0, True),
        free_mb=9100,
        total_mb=12282,
        allow_unmeasured=False,
        **{**common, "width": 1536, "height": 1536},
    )
    assert a.risk == "high" and a.would_reject is True and "OOM" in a.advice
    a = assess(
        TABLE.estimate("pulid", 2048, 2048, 1.0, True),
        free_mb=9100,
        total_mb=12282,
        allow_unmeasured=False,
        **{**common, "width": 2048, "height": 2048},
    )
    assert a.risk == "unknown" and a.would_reject is True and "未実測" in a.advice
    a = assess(
        TABLE.estimate("pulid", 2048, 2048, 1.0, True),
        free_mb=9100,
        total_mb=12282,
        allow_unmeasured=True,
        **{**common, "width": 2048, "height": 2048},
    )
    assert a.risk == "unknown" and a.would_reject is False


def test_table_bound_to_its_checkpoint_and_lora(tmp_path: Path) -> None:
    """A table measured with one checkpoint (and no LoRA) must not vouch for another checkpoint or an added LoRA."""
    import json

    data = json.loads((Path(__file__).parent / "fixtures" / "vram_table.test.json").read_text())
    data["checkpoint"] = "measured_xl.safetensors"
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data))
    table = VramTable.load(path)
    same = table.estimate("pulid", 832, 1216, 1.0, True, checkpoint="measured_xl.safetensors")
    assert same.basis == "measured" and same.peak_mb == 8600
    other = table.estimate("pulid", 832, 1216, 1.0, True, checkpoint="other_fp32.safetensors")
    assert other.basis == "unknown" and other.peak_mb is None and other.reason and "checkpoint" in other.reason
    with_lora = table.estimate(
        "pulid", 832, 1216, 1.0, True, checkpoint="measured_xl.safetensors", lora="x.safetensors"
    )
    assert with_lora.basis == "unknown" and with_lora.reason and "LoRA" in with_lora.reason
    a = assess(
        other,
        free_mb=11000,
        total_mb=12282,
        margin_mb=512,
        allow_unmeasured=False,
        method="pulid",
        width=832,
        height=1216,
        upscale=1.0,
        face_detailer=True,
    )
    assert a.would_reject and "measured_xl.safetensors" in a.advice
    # tables without the fields (older measurements) behave as before
    assert TABLE.estimate("pulid", 832, 1216, 1.0, True, checkpoint="anything").basis == "measured"
