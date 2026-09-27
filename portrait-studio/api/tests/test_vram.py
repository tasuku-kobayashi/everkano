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
