"""Streamlit smoke tests that execute UI code without a browser."""

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_app_starts_without_exception() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=30).run()
    assert not app.exception


def test_dataset_batch_classification_workspace_renders() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=30).run()

    app.segmented_control[0].set_value("Batch classify").run(timeout=30)

    assert not app.exception
    assert any(item.value == "Batch visual classification" for item in app.subheader)
    checkbox_labels = [item.label for item in app.checkbox]
    info_messages = [item.value for item in app.info]
    assert "Select image" in checkbox_labels or any(
        message.startswith("No unreviewed samples in ") for message in info_messages
    )
    assert app.title[0].value == "分子结构识别与性质分析"
    assert len(app.tabs) == 0
    source = app_path.read_text(encoding="utf-8")
    review_source = (app_path.parent / "src" / "ui" / "dataset_review_page.py").read_text(encoding="utf-8")
    assert 'st.checkbox("Select image"' in review_source
    assert '"Machine rejected"' in review_source
    assert "submit_recheck_batch" in review_source
    assert "PAGE_LABELS" in source
    assert "active_page" in source
    assert "st.tabs" not in source
    assert "st.navigation" not in source


def test_smiles_result_page_renders_without_exception(tmp_path: Path) -> None:
    """Execute result widgets directly so image/dataframe API errors cannot hide."""
    from app import show_report
    from src.analysis.molecule_report import MoleculeReportGenerator

    report = MoleculeReportGenerator("demo", tmp_path).generate(smiles="CCO")
    assert report["status"] == "success"
    show_report(report, show_preprocessing=False, export_pdf=False, key_prefix="test_smiles")


def test_app_avoids_newer_only_stretch_width_api() -> None:
    """Keep the UI compatible with the project's existing Streamlit 1.41 environment."""
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    source = app_path.read_text(encoding="utf-8")
    assert 'width="stretch"' not in source


def test_streamlit_correction_widgets_are_present() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "report_view.py").read_text(encoding="utf-8")
    assert "校验并应用修正" in source
    assert "恢复模型原始结果" in source
    assert "确认与修正" in source
    assert "纠错反馈与数据回流" not in source
    assert "保存为待审核" not in source
    assert "确认进入训练集" not in source
    assert "多后端候选与一致性" in source
    assert "确认结构正确" in source
    assert "修改 SMILES" in source
    assert "无法确认" in source
    assert "撤销确认" in source
    assert "重新修改结构" in source


def test_candidate_and_strategy_updates_rerun_immediately() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "report_view.py").read_text(encoding="utf-8")
    assert "def _apply_report_update_and_rerun" in source
    assert "st.rerun()" in source
    assert "user_selected_{backend}_candidate" in source
    assert "strategy_selection" in source


def test_batch_skip_button_matches_worker_behavior_and_autorefresh() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "batch_page.py").read_text(encoding="utf-8")
    assert "跳过下一张未开始文件" in source
    assert "跳过当前文件" not in source
    assert "time.sleep" not in source
    assert '@st.fragment(run_every="3s")' in source
    assert "def _render_live_job_status" in source
    assert "RUNNING_BATCH_STATUSES" in source
    fragment_body = source.split("def _render_live_job_status", 1)[1].split("def _is_running_batch_status", 1)[0]
    assert "start_batch_job" not in fragment_body
    assert "uploaded_files" not in fragment_body
    assert "明确需要审核" in source
    assert "人工审核总数" in source
    assert "def _batch_status_counts" in source


def test_history_delete_actions_distinguish_index_and_files() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "history_page.py").read_text(encoding="utf-8")
    assert "从历史中移除" in source
    assert "删除记录及本地文件" in source
    assert "确认删除本地文件" in source
    assert "重试删除残留文件" in source
    assert "报告文件和运行目录已保留" in source
    assert "artifact_status" in source
    assert "disabled=not report_available" in source
    assert "报告文件已过期" in source


def test_review_queue_supports_return_revision_loop() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "review_queue_page.py").read_text(encoding="utf-8")
    assert "审核人" in source
    assert "打开原报告" in source
    assert "修订 SMILES" in source
    assert "重新提交审核" in source
    assert "disabled=reviewer_missing" in source
    assert "revised_by" in source
    assert "revise_and_resubmit" in source


def test_image_editor_supports_visual_two_point_crop() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "image_editor.py").read_text(encoding="utf-8")
    assert "components.html" in source
    assert "crop_editor_key" in source
    assert "点击两个角点" in source
    assert "crop_bbox_from_points" in source


def test_document_page_uses_chinese_mode_labels() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "document_page.py").read_text(encoding="utf-8")
    assert "全文检测与审核识别" in source
    assert "开始{DOCUMENT_WORKFLOW_LABEL}" in source
    assert "仅检测分子区域（速度快，不执行结构识别）" not in source
    assert "检测并识别已确认分子区域（新检测结果需先人工确认）" not in source
    assert 'st.radio("处理模式"' not in source
    assert "论文页码（全文共" in source
    assert "所有页面均已保留在当前任务中" in source
    assert "Update bbox and rerun" not in source
    assert "Delete region" not in source
    assert "确认并识别" not in source
    assert "仅保存框选" in source
    assert "识别当前区域" in source
    assert "空白处拖动画新框" in source
    assert "框选调整已保存" in source
    assert "裁剪预览" in source
    assert '@st.fragment(run_every="2s")' in source
    assert "document_region_job" in source
    assert "当前阶段" in source
    assert "取消当前识别任务" in source
    assert "重试最近识别任务" in source
    assert "识别本页已确认区域" in source
    assert "批量识别全部已确认区域" in source
    assert "未确认框不会进入 OCSR" in source
    render_page_body = source.split("def render_document_page", 1)[1].split("def _run_demo_document", 1)[0]
    inspector_body = source.split("def _render_region_inspector", 1)[1].split("def _region_candidate_smiles", 1)[0]
    assert "_render_region_ocsr_job_status()" not in render_page_body
    assert "_render_region_ocsr_job_status(selected_id)" in inspector_body
    assert "合并两个或多个区域" in source
    assert "拆分当前区域" in source
    assert "JSON 检测训练标注" in source


def test_document_subprocess_json_parser_ignores_trailing_logs() -> None:
    from src.ui.document_page import _extract_document_progress, _extract_json_object, _extract_region_progress

    parsed = _extract_json_object('native log\n{"status": "success", "result_path": "x.json"}\n[09:46] warning')
    assert parsed == {"status": "success", "result_path": "x.json"}
    progress = _extract_document_progress(
        'native log\nDOCUMENT_PROGRESS_JSON={"stage":"detecting","current":12,"total":30,"detail":"第12页"}\n'
    )
    assert progress == {"stage": "detecting", "current": 12, "total": 30, "detail": "第12页"}

    mixed = (
        'DOCUMENT_PROGRESS_JSON={"stage":"detecting","current":30,"total":30}\n'
        '{"status":"success","result_path":"final.json","summary":{"page_count":30}}\n'
    )
    assert _extract_json_object(mixed)["result_path"] == "final.json"
    marked = (
        'DOCUMENT_PROGRESS_JSON={"stage":"detecting","current":1,"total":2}\n'
        'DOCUMENT_RESULT_JSON={"status":"success","result_path":"marked.json"}\n'
    )
    assert _extract_json_object(marked)["result_path"] == "marked.json"
    region_progress = _extract_region_progress(
        'noise\nDOCUMENT_REGION_PROGRESS_JSON={"stage":"recognizing","current":2,"total":5,"region_id":"p002_r004"}\n'
    )
    assert region_progress == {"stage": "recognizing", "current": 2, "total": 5, "region_id": "p002_r004"}


def test_document_result_recovery_uses_completed_worker_log(tmp_path: Path) -> None:
    from src.ui.document_page import _latest_recoverable_document_result

    result_path = tmp_path / "document_result.json"
    result_path.write_text('{"document_id":"doc"}', encoding="utf-8")
    job_dir = tmp_path / "ui_jobs" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "stdout.log").write_text(
        'DOCUMENT_PROGRESS_JSON={"stage":"detecting","current":15,"total":15}\n'
        + 'DOCUMENT_RESULT_JSON={"status":"success","result_path":'
        + json.dumps(str(result_path))
        + ',"summary":{"page_count":15,"region_count":377}}\n',
        encoding="utf-8",
    )

    recovered = _latest_recoverable_document_result(tmp_path / "ui_jobs")

    assert recovered is not None
    payload, recovered_path = recovered
    assert payload["summary"] == {"page_count": 15, "region_count": 377}
    assert recovered_path == result_path


def test_document_region_filter_does_not_fall_back_to_nonmatching_regions() -> None:
    from src.ui.document_page import _filter_document_regions

    regions = [
        {"region_id": "text", "region_type": "text", "status": "detected", "confirmed": False},
        {"region_id": "reaction", "region_type": "reaction_like", "status": "recognized", "confirmed": True},
    ]

    assert _filter_document_regions(regions, "molecule", "全部") == []
    assert [region["region_id"] for region in _filter_document_regions(regions, "reaction", "全部")] == ["reaction"]
    assert _filter_document_regions(regions, "全部", "识别失败") == []


def test_document_region_list_renders_empty_state_without_radio(monkeypatch) -> None:
    from src.ui import document_page

    selections = iter(["molecule", "全部"])
    messages: list[str] = []
    monkeypatch.setattr(document_page.st, "subheader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(document_page.st, "selectbox", lambda *_args, **_kwargs: next(selections))
    monkeypatch.setattr(document_page.st, "info", lambda message, **_kwargs: messages.append(str(message)))

    def unexpected_radio(*_args, **_kwargs):
        raise AssertionError("空筛选结果不应回退并渲染区域单选列表")

    monkeypatch.setattr(document_page.st, "radio", unexpected_radio)
    selected = document_page._render_region_list(
        [{"region_id": "text", "region_type": "text", "status": "detected", "confirmed": False}],
        "text",
    )

    assert selected is None
    assert messages == ["没有匹配区域。"]


def test_document_region_label_exposes_screening_diagnostics() -> None:
    from src.ui.document_page import _compact_region_option_label, _region_option_label, _short_text_false_positive

    region = {
        "page_number": 2,
        "region_id": "p002_r003",
        "region_type": "text",
        "status": "detected",
        "screening": {
            "reason_codes": ["short_text_hard_reject", "pdf_text_token"],
            "diagnostics": {"long_line_count": 1, "valid_component_count": 2},
        },
    }

    label = _region_option_label(region)
    assert "线段 1" in label
    assert "组件 2" in label
    assert "短文本硬拒绝" in label
    compact_label = _compact_region_option_label(region)
    assert compact_label.startswith("p002_r003 · 文本 · 已检测")
    assert "线段" not in compact_label
    assert _short_text_false_positive(region) is True


def test_document_advanced_filters_share_one_compact_canvas_layout() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "document_page.py").read_text(encoding="utf-8")
    workbench = source[source.index("def _document_workbench") : source.index("def _document_needs_screening_refresh")]

    assert "selected, filtered = _render_region_navigator" in workbench
    assert 'st.columns([0.72, 0.28], gap="large")' in workbench
    assert '_render_bbox_dragger(page, filtered, "", [0, 0, 1, 1])' in workbench
    assert '_render_bbox_dragger(page, filtered, selected["region_id"], preview_bbox)' in workbench
    assert "_render_bbox_dragger(page, visible" not in workbench
    assert "类型和状态筛选会同步作用于区域导航与画布框" in workbench


def test_document_canvas_exposes_human_friendly_edit_controls() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui" / "document_page.py").read_text(encoding="utf-8")

    assert "＋ 新增框模式" in source
    assert "方向键微调" in source
    assert "Esc 取消当前拖动" in source
    assert "删除当前框（Delete）" in source
    assert "确认新增" in source
    assert "取消新增" in source
    assert "保存调整" in source
    assert "取消调整" in source
    assert 'submitEvent("select"' not in source
    assert "松手后自动保存" not in source
    assert "批量清理短文本误框" in source
    assert "拖动画布" in source
    assert 'id="zoom-in"' in source
    assert "↶ 撤销" in source
    assert "复制区域" in source


def test_batch_page_exposes_resumable_review_and_confirmation_gated_exports() -> None:
    root = Path(__file__).resolve().parents[1]
    page_source = (root / "src" / "ui" / "batch_page.py").read_text(encoding="utf-8")
    registry_source = (root / "src" / "runtime" / "job_registry.py").read_text(encoding="utf-8")

    assert "上传多张图片或 ZIP" in page_source
    assert 'accept_multiple_files="directory"' in page_source
    assert "服务器本地文件夹路径" in page_source
    assert "暂停" in page_source and "继续/断点续跑" in page_source
    assert "预计剩余" in page_source and "当前文件" in page_source
    assert "批量确认所选候选" in page_source
    assert "校验并应用修正" in page_source
    assert "重新识别所选文件" in page_source
    assert "下载已确认结构 SMI" in page_source
    assert "下载完整结果 ZIP" in page_source
    assert "st.dataframe" not in page_source
    assert "st.table" not in page_source
    assert "_render_batch_result_rows" in page_source
    assert 'st.status("正在启动批量任务…"' in page_source
    assert 'with st.expander(f"查看文件清单（{len(entries)} 项）", expanded=False)' in page_source
    assert 'with st.expander(f"缩略图预览（前 {len(previews)} 张）", expanded=False)' in page_source
    assert 'caption="原图", width=280' in page_source
    assert 'caption="候选结构", width=280' in page_source
    assert "系统会使用清晰增强图参与 OCSR" in page_source
    assert 'env["MOLSCRIBE_ISOLATED_SUBPROCESS"] = "false"' in registry_source
    assert 'env["OCSR_GPU_MAX_CONCURRENT_INFERENCE"] = "1"' in registry_source


def test_batch_page_restores_active_job(monkeypatch) -> None:
    from src.ui import batch_page

    class FakeStore:
        def exists(self, job_id: str) -> bool:
            return job_id == "batch-demo"

    expected = {"job_id": "batch-demo", "status": "running"}
    monkeypatch.setattr(batch_page.st, "session_state", {"batch_job_id": "batch-demo"})
    monkeypatch.setattr(batch_page, "refresh_batch_job", lambda job_id, store: expected)

    assert batch_page._active_job(FakeStore()) == expected


def test_document_navigation_strict_filter_copy_quality_and_duplicates() -> None:
    from src.ui.document_page import (
        _copy_region_edit,
        _crop_quality_warnings,
        _duplicate_region_groups,
        _is_strict_molecule_candidate,
        _thumbnail_window,
    )

    assert _thumbnail_window(list(range(1, 21)), 10) == [7, 8, 9, 10, 11, 12, 13]
    assert _thumbnail_window([1, 2, 3], 2) == [1, 2, 3]

    strict = {
        "region_type": "molecule",
        "source": "detector",
        "screening": {"passed": True, "structural_evidence": True},
    }
    weak = {
        "region_type": "molecule",
        "source": "detector",
        "screening": {"passed": True, "structural_evidence": False},
    }
    assert _is_strict_molecule_candidate(strict) is True
    assert _is_strict_molecule_candidate(weak) is False
    assert _is_strict_molecule_candidate({**weak, "source": "user"}) is True

    document = {
        "pages": [
            {"page_number": 1, "width": 1000, "height": 2000},
            {"page_number": 2, "width": 2000, "height": 1000},
        ]
    }
    region = {"region_id": "p001_r001", "page_number": 1, "bbox": [100, 200, 500, 1000], "region_type": "molecule"}
    copied = _copy_region_edit(document, region, 2)
    assert copied["bbox"] == [200, 100, 1000, 500]
    assert copied["confirmed"] is False

    duplicates = _duplicate_region_groups([
        {"region_id": "a", "page_number": 1, "bbox": [10, 10, 110, 110], "region_type": "molecule"},
        {"region_id": "b", "page_number": 1, "bbox": [12, 12, 108, 108], "region_type": "molecule"},
        {"region_id": "c", "page_number": 2, "bbox": [12, 12, 108, 108], "region_type": "molecule"},
    ])
    assert duplicates == [["a", "b"]]

    warnings = _crop_quality_warnings(
        {"width": 2000, "height": 3000},
        [10, 10, 30, 35],
        {"screening": {"reason_codes": ["missing_skeleton_evidence"]}},
    )
    assert any("尺寸过小" in message for message in warnings)
    assert any("骨架证据" in message for message in warnings)


def test_old_document_screening_is_detected_for_non_ocsr_refresh() -> None:
    from src.ui.document_page import _document_needs_screening_refresh

    old = {
        "regions": [{
            "region_id": "p001_r001",
            "source": "detector",
            "status": "detected",
            "confirmed": False,
            "screening": {"config_version": "crop-screening-candidate-v2"},
        }]
    }
    current = {
        "processing": {"screening_refresh": {"config_version": "crop-screening-candidate-v3"}},
        "regions": old["regions"],
    }

    assert _document_needs_screening_refresh(old) is True
    assert _document_needs_screening_refresh(current) is False
