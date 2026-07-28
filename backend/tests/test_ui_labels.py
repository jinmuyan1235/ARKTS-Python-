"""Tests for UI-only labels and backend availability rules."""

from pathlib import Path

from src.ui.labels import REGION_TYPE_LABELS, backend_label, default_backend, runnable_backends, unavailable_backends


def test_backend_chinese_display_mapping_preserves_internal_values() -> None:
    assert backend_label("demo") == "演示模式（仅内置样例，不是真实识别）"
    assert backend_label("molscribe") == "MolScribe 图像识别"
    assert backend_label("decimer") == "DECIMER 图像识别"
    assert backend_label("ensemble") == "多模型联合识别"
    assert backend_label("custom_backend") == "custom_backend"


def test_unavailable_backends_are_not_runnable() -> None:
    statuses = {
        "demo": {"available": True},
        "molscribe": {"available": False},
        "decimer": {"available": True},
        "ensemble": {"available": False},
    }
    assert runnable_backends(statuses) == ["decimer"]
    assert "molscribe" in unavailable_backends(statuses)


def test_ensemble_requires_two_real_backends() -> None:
    one_real = {
        "demo": {"available": True},
        "molscribe": {"available": False},
        "decimer": {"available": True},
        "ensemble": {"available": True},
    }
    two_real = {
        "demo": {"available": True},
        "molscribe": {"available": True},
        "decimer": {"available": True},
        "ensemble": {"available": True},
    }
    assert "ensemble" not in runnable_backends(one_real)
    assert "ensemble" in unavailable_backends(one_real)
    assert "ensemble" in runnable_backends(two_real)


def test_configured_backend_is_default_when_available() -> None:
    statuses = {
        "demo": {"available": True},
        "molscribe": {"available": True},
        "decimer": {"available": True},
        "ensemble": {"available": True},
    }
    assert default_backend(statuses, configured="molscribe") == "molscribe"


def test_demo_is_default_only_without_real_backends() -> None:
    statuses = {
        "demo": {"available": True},
        "molscribe": {"available": False},
        "decimer": {"available": False},
        "ensemble": {"available": False},
    }
    assert runnable_backends(statuses) == ["demo"]
    assert default_backend(statuses, configured="decimer") == "demo"


def test_production_mode_can_disable_demo_fallback() -> None:
    statuses = {
        "demo": {"available": True},
        "molscribe": {"available": False},
        "decimer": {"available": False},
        "ensemble": {"available": False},
    }
    assert runnable_backends(statuses, allow_demo_fallback=False) == []
    assert default_backend(statuses, configured="demo", allow_demo_fallback=False) == ""


def test_image_preview_widths_are_limited() -> None:
    from src.ui import image_viewer

    assert image_viewer.UPLOAD_PREVIEW_WIDTH == 600
    assert image_viewer.STRUCTURE_PREVIEW_WIDTH == 480
    assert image_viewer.PREPROCESS_PREVIEW_WIDTH == 260
    assert image_viewer.DOCUMENT_PREVIEW_WIDTH == 900


def test_batch_default_table_headers_are_chinese() -> None:
    from src.ui.batch_page import default_batch_columns_chinese

    assert default_batch_columns_chinese() == [
        "文件名",
        "状态",
        "识别后端",
        "最终 SMILES",
        "是否有效",
        "置信度",
        "推理耗时(ms)",
        "失败原因",
    ]


def test_document_region_type_labels_cover_reaction_workflow() -> None:
    assert REGION_TYPE_LABELS["reaction"] == "反应式"
    assert REGION_TYPE_LABELS["reaction_arrow"] == "反应箭头"
    assert REGION_TYPE_LABELS["reaction_condition"] == "反应条件"
    assert REGION_TYPE_LABELS["figure"] == "普通图像/插图"
    assert REGION_TYPE_LABELS["ignore"] == "忽略"


def test_batch_chart_source_uses_chinese_labels() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "analysis" / "batch_analyzer.py").read_text(encoding="utf-8")
    assert "批量处理统计" in source
    assert "识别成功" in source
    assert "有效 SMILES" in source
    assert "识别失败" in source
