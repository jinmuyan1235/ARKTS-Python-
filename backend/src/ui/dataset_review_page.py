"""Streamlit workspace for visual OCSR review and trusted-label confirmation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps
import streamlit as st

import config
from src.datasets.solo_review import REGION_TYPES, VISUAL_REVIEW_STATUSES, SoloReviewStore
from src.ui.page_annotation_page import render_page_annotation_workspace


BATCH_CLASS_TO_VISUAL_STATUS = {
    region_type: "valid_single_molecule_crop" if region_type == "molecule" else region_type
    for region_type in REGION_TYPES
}


def render_dataset_review_page() -> None:
    """Render the review page without treating model output as chemical truth."""
    st.header("OCSR Data Review")
    st.info(
        "Visual Review builds molecule-vs-negative detector/rejector labels. "
        "Exact OCSR training and accuracy evaluation additionally require a trusted structure label; "
        "model predictions are evidence, not ground truth."
    )
    if notice := st.session_state.pop("solo_review_notice", None):
        st.success(str(notice))
    if error := st.session_state.pop("solo_review_error", None):
        st.error(str(error))
    configured_dataset_root = os.getenv("OCSR_REVIEW_DATASET_ROOT")
    configured_review_root = os.getenv("OCSR_REVIEW_ROOT")
    store = SoloReviewStore(
        configured_dataset_root or config.DATA_DIR / "ocsr_collections",
        review_root=configured_review_root or config.DATA_DIR / "review",
        strict_dataset_root=bool(configured_dataset_root or configured_review_root),
    )
    if configured_dataset_root or configured_review_root:
        st.caption(f"Isolated dataset: {store.dataset_root} | review ledger: {store.review_root}")
    mode = st.segmented_control(
        "Review mode",
        ["Queue", "Batch classify", "Delayed recheck", "Page annotation"],
        default="Queue",
        key="solo_review_mode",
    )
    if mode == "Page annotation":
        render_page_annotation_workspace()
    elif mode == "Delayed recheck":
        _render_recheck_workspace(store)
    elif mode == "Batch classify":
        _render_batch_workspace(store)
    else:
        _render_queue_workspace(store)


def _render_queue_workspace(store: SoloReviewStore) -> None:
    stats = store.queue_stats()
    metrics = st.columns(8)
    for column, label, key in zip(
        metrics,
        (
            "Total", "Machine-routed visual", "Visual remaining", "Machine gate passed",
            "Pending machine", "Visual reviewed", "Machine rejected", "Visual negatives",
        ),
        (
            "total", "machine_routed_visual", "visual_remaining", "machine_verified",
            "pending_machine", "reviewed", "machine_rejected", "visual_negative",
        ),
    ):
        column.metric(label, stats[key])
    st.caption(
        f"Detector candidates: {stats['positive_candidates']} molecule / "
        f"{stats['negative_candidates']} negative; trusted structure labels: {stats['trusted_structure_labels']}."
    )
    controls = st.columns([0.36, 0.22, 0.42])
    scope_label = controls[0].selectbox(
        "Review range",
        ["Visual remaining", "Machine gate passed", "Pending machine review", "Machine rejected", "All samples"],
        index=0,
        key="solo_review_scope",
    )
    scope = {
        "Visual remaining": "pending_human_review", "Machine gate passed": "machine_verified",
        "Pending machine review": "pending_machine_review", "Machine rejected": "machine_rejected",
        "All samples": "all_samples",
    }[scope_label]
    show_reviewed = controls[1].toggle("Show reviewed", value=False, key="solo_show_reviewed")
    ids = store.list_item_ids(scope=scope, include_reviewed=show_reviewed)
    if not ids:
        st.info("No matching reviewable samples in data/review/machine_review_manifest.csv.")
        return
    selected = controls[2].selectbox("Sample", ids, key="solo_queue_sample")
    item = store.get_item(str(selected))
    if item is None:
        st.warning("The selected sample is no longer available. Refresh the queue.")
        return
    _render_item(store, item, recheck=False)


def _render_recheck_workspace(store: SoloReviewStore) -> None:
    controls = st.columns([0.18, 0.14, 0.18, 0.18, 0.32])
    proportion = controls[0].number_input("Recheck ratio", min_value=0.0, max_value=1.0, value=0.1, step=0.05)
    seed = controls[1].number_input("Seed", min_value=0, value=7, step=1)
    max_samples = int(controls[2].number_input("Maximum samples", min_value=0, value=0, step=1, help="0 means no additional cap."))
    if controls[3].button("Create recheck queue", type="secondary"):
        result = store.create_recheck_queue(
            float(proportion), seed=int(seed), max_samples=max_samples or None,
        )
        st.success(f"Selected {result['selected']} samples")
        st.rerun()
    items = store.list_recheck_items()
    controls[4].metric("Pending rechecks", len(items))
    if not items:
        st.info("No pending delayed rechecks.")
        return
    mode = st.segmented_control("Recheck mode", ["Batch recheck", "Single recheck"], default="Batch recheck", key="recheck_mode")
    if mode == "Single recheck":
        selected = st.selectbox("Recheck sample", [str(item["sample_id"]) for item in items], key="solo_recheck_sample")
        _render_item(store, next(item for item in items if item["sample_id"] == selected), recheck=True)
        return
    displayed = items[:32]
    checkbox_keys = {str(item["sample_id"]): f"recheck_batch_pick_{item['sample_id']}" for item in displayed}
    selection_controls = st.columns([0.20, 0.20, 0.60])
    if selection_controls[0].button("Select displayed", key="select_recheck_batch"):
        for key in checkbox_keys.values():
            st.session_state[key] = True
    if selection_controls[1].button("Clear selection", key="clear_recheck_batch"):
        for key in checkbox_keys.values():
            st.session_state[key] = False
    selected_ids = _render_batch_thumbnails(store, displayed, checkbox_keys)
    selection_controls[2].metric("Selected rechecks", len(selected_ids))
    with st.form("batch_recheck_form", border=True):
        action = st.columns([0.30, 0.30, 0.40])
        action[0].selectbox("Apply recheck class", REGION_TYPES, key="batch_recheck_target")
        action[1].text_input("Recheck notes", key="batch_recheck_notes")
        action[2].warning(f"This independently rechecks {len(selected_ids)} selected sample(s).")
        st.form_submit_button(
            "Save batch recheck", type="primary", disabled=not selected_ids,
            on_click=_save_batch_recheck,
            args=(store, selected_ids, "batch_recheck_target", "batch_recheck_notes", checkbox_keys),
        )


def _render_batch_workspace(store: SoloReviewStore) -> None:
    st.subheader("Batch visual classification")
    st.caption(
        "Filter and inspect machine-routed, machine-rejected, or already reviewed samples, "
        "then apply one visual class to the selected images."
    )
    source_label = st.selectbox(
        "Batch source", ["Machine-routed visual", "Machine rejected", "Reviewed samples"], key="batch_source",
    )
    source_scope = {
        "Machine-routed visual": "pending_human_review",
        "Machine rejected": "machine_rejected",
        "Reviewed samples": "all_samples",
    }[source_label]
    correction_mode = source_label == "Reviewed samples"
    summaries = store.list_item_summaries(scope=source_scope, include_reviewed=correction_mode)
    if correction_mode:
        summaries = [row for row in summaries if row["visual_review_status"]]
    if not summaries:
        if correction_mode:
            st.info("No reviewed samples are available for batch correction.")
        else:
            st.info(f"No unreviewed samples in {source_label}.")
        return
    categories = sorted({row["category"] for row in summaries})
    visual_statuses = sorted({row["visual_review_status"] for row in summaries if row["visual_review_status"]})
    filters = st.columns([0.26, 0.24, 0.16, 0.16, 0.18])
    visual_status = filters[0].selectbox(
        "Current visual class", ["All", *visual_statuses],
        key="batch_current_visual_class", disabled=not correction_mode,
    )
    category = filters[1].selectbox("Machine category", ["All", *categories], key="batch_machine_category")
    page_size = int(filters[2].selectbox("Page size", [8, 12, 20, 32], index=2, key="batch_page_size"))
    filtered = summaries
    if correction_mode and visual_status != "All":
        filtered = [row for row in filtered if row["visual_review_status"] == visual_status]
    if category != "All":
        filtered = [row for row in filtered if row["category"] == category]
    page_count = max(1, (len(filtered) + page_size - 1) // page_size)
    page_number = int(filters[3].number_input(
        "Page",
        min_value=1,
        max_value=page_count,
        value=1,
        step=1,
        key=f"batch_page_number_{source_label}_{visual_status}_{category}_{page_size}",
    ))
    filters[4].metric("Matching", len(filtered))
    displayed = filtered[(page_number - 1) * page_size:page_number * page_size]
    displayed_ids = [row["sample_id"] for row in displayed]
    page_key = f"{source_scope}_{visual_status}_{category}_{page_size}_{page_number}"
    checkbox_keys = {sample_id: f"batch_pick_{source_scope}_{sample_id}" for sample_id in displayed_ids}
    selection_controls = st.columns([0.20, 0.20, 0.60])
    if selection_controls[0].button("Select displayed", key=f"select_batch_page_{page_key}"):
        for key in checkbox_keys.values():
            st.session_state[key] = True
    if selection_controls[1].button("Clear selection", key=f"clear_batch_page_{page_key}"):
        for key in checkbox_keys.values():
            st.session_state[key] = False
    selected_ids = _render_batch_thumbnails(store, displayed, checkbox_keys)
    selection_controls[2].metric("Selected on this page", len(selected_ids))
    target_default = category if category in REGION_TYPES else "molecule"
    target_key = f"batch_target_{category}_{page_number}"
    reviewer_key = f"batch_reviewer_{category}_{page_number}"
    notes_key = f"batch_notes_{category}_{page_number}"
    with st.form(key=f"batch_classification_form_{source_label}_{category}_{page_size}_{page_number}", border=True):
        action = st.columns([0.28, 0.22, 0.50])
        action[0].selectbox(
            "Apply class",
            REGION_TYPES,
            index=REGION_TYPES.index(target_default),
            key=target_key,
        )
        action[1].text_input("Reviewer", value="local", key=reviewer_key)
        action[2].text_input("Batch notes", key=notes_key)
        if correction_mode:
            st.warning(
                f"Correction mode replaces the existing visual audit for {len(selected_ids)} selected sample(s) "
                "and rebuilds the exported review manifests."
            )
        else:
            st.warning(f"This will classify {len(selected_ids)} selected sample(s) with the same label.")
        st.form_submit_button(
            "Save batch classification",
            type="primary",
            disabled=not selected_ids,
            on_click=_save_batch_review,
            args=(store, selected_ids, target_key, reviewer_key, notes_key, checkbox_keys),
        )


def _render_batch_thumbnails(
    store: SoloReviewStore,
    summaries: list[dict[str, str]],
    checkbox_keys: dict[str, str],
) -> list[str]:
    selected_ids: list[str] = []
    columns = st.columns(4, gap="medium")
    for index, summary in enumerate(summaries):
        with columns[index % 4]:
            sample_id = summary["sample_id"]
            with st.container(border=True):
                selected = st.checkbox("Select image", key=checkbox_keys[sample_id])
                if selected:
                    selected_ids.append(sample_id)
                path = store.resolve_dataset_path(summary["image_path"], dataset_root=summary["dataset_root"])
                if path:
                    try:
                        modified = Path(path).stat().st_mtime_ns
                    except OSError:
                        modified = 0
                    st.image(_batch_thumbnail(path, modified), width="stretch")
                else:
                    st.info("Image missing")
                st.caption(
                    f"machine={summary['category']} · reviewed={summary.get('visual_review_status') or '-'} · "
                    f"quality={summary['image_quality_level'] or '-'} · …{sample_id[-12:]}"
                )
                with st.expander("Sample details", expanded=False):
                    st.code(sample_id, language=None)
    return selected_ids


@st.cache_data(show_spinner=False)
def _batch_thumbnail(path: str, modified_time_ns: int, size: tuple[int, int] = (480, 300)) -> Image.Image:
    """Fit arbitrary crop shapes onto an equal-size canvas for an aligned grid."""
    del modified_time_ns  # Included in the cache key so replaced source files invalidate the thumbnail.
    with Image.open(path) as image:
        source = image.convert("RGB")
    inner_size = (size[0] - 24, size[1] - 24)
    fitted = ImageOps.contain(source, inner_size, method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, "white")
    left = (size[0] - fitted.width) // 2
    top = (size[1] - fitted.height) // 2
    canvas.paste(fitted, (left, top))
    return canvas


def _save_batch_review(
    store: SoloReviewStore,
    selected_ids: list[str],
    target_key: str,
    reviewer_key: str,
    notes_key: str,
    checkbox_keys: dict[str, str],
) -> None:
    """Persist a selected thumbnail page before Streamlit's submit rerun."""
    try:
        region_type = str(st.session_state[target_key])
        result = store.submit_visual_batch(
            selected_ids,
            visual_review_status=BATCH_CLASS_TO_VISUAL_STATUS[region_type],
            region_type=region_type,
            reviewer=str(st.session_state.get(reviewer_key) or "local"),
            review_notes=str(st.session_state.get(notes_key) or ""),
        )
        for key in checkbox_keys.values():
            st.session_state[key] = False
        st.session_state["solo_review_notice"] = f"Batch classified {result['reviewed_count']} samples as {region_type}."
        st.session_state.pop("solo_review_error", None)
    except Exception as exc:
        st.session_state["solo_review_error"] = str(exc)


def _save_batch_recheck(
    store: SoloReviewStore,
    selected_ids: list[str],
    target_key: str,
    notes_key: str,
    checkbox_keys: dict[str, str],
) -> None:
    """Persist one independent delayed-review class for selected images."""
    try:
        region_type = str(st.session_state[target_key])
        result = store.submit_recheck_batch(
            selected_ids,
            visual_review_status=BATCH_CLASS_TO_VISUAL_STATUS[region_type],
            region_type=region_type,
            review_notes=str(st.session_state.get(notes_key) or ""),
        )
        for key in checkbox_keys.values():
            st.session_state[key] = False
        st.session_state["solo_review_notice"] = f"Batch rechecked {result['reviewed_count']} samples as {region_type}."
        st.session_state.pop("solo_review_error", None)
    except Exception as exc:
        st.session_state["solo_review_error"] = str(exc)


def _render_item(store: SoloReviewStore, item: dict[str, Any], *, recheck: bool) -> None:
    st.subheader(str(item.get("sample_id") or "Sample"))
    status_columns = st.columns(4)
    status_columns[0].metric("Queue status", item.get("verification_status") or "-")
    status_columns[1].metric("Visual review", item.get("audit", {}).get("visual_review_status") or "Not reviewed")
    status_columns[2].metric("Quality", item.get("image_quality_level") or "-")
    status_columns[3].metric("Machine category", item.get("machine_category") or item.get("category") or "-")
    _render_file_status(item)
    _render_images(item)
    if os.getenv("OCSR_VISUAL_ONLY_REVIEW", "").strip().lower() in {"1", "true", "yes", "on"}:
        st.info("Visual-only review: MolScribe, DECIMER, and ensemble outputs are hidden to avoid label bias.")
    else:
        _render_predictions(store, item)
    _render_details(item)
    if not recheck and item.get("audit"):
        with st.expander("Latest review audit", expanded=False):
            st.json(item["audit"])
    _render_actions(store, item, recheck=recheck)


def _render_file_status(item: dict[str, Any]) -> None:
    st.caption("Source file resolution")
    lines: list[str] = []
    for label, info in (("Original document page", item.get("page_path_info", {})), ("Candidate image", item.get("image_path_info", {})), ("Candidate crop", item.get("crop_path_info", {}))):
        lines.extend((
            f"{label}: {'exists' if info.get('exists') else 'missing'}",
            f"  manifest path: {info.get('manifest_path', '')}",
            f"  dataset root: {info.get('dataset_root', item.get('dataset_root_resolved', ''))}",
            f"  resolved path: {info.get('resolved_path', '')}",
        ))
    # Avoid Streamlit's Arrow serialization here: it has crashed in this WSL runtime.
    st.code("\n".join(lines), language=None)
    if not item.get("files_complete"):
        st.warning("A required source file is missing. Visual status is suggested as missing_source_file and ground-truth acceptance is disabled.")


def _render_images(item: dict[str, Any]) -> None:
    columns = st.columns(3)
    page = item.get("page_path_abs")
    bbox = _parse_json(item.get("bbox"), [])
    with columns[0]:
        st.caption("Original document page with bbox")
        if page:
            st.image(_page_with_bbox(Path(str(page)), bbox), use_container_width=True)
        else:
            st.info("Original document page is unavailable.")
    with columns[1]:
        st.caption("Candidate crop from source")
        if item.get("crop_path_abs"):
            st.image(str(item["crop_path_abs"]), use_container_width=True)
        else:
            st.info("Candidate crop is unavailable.")
    with columns[2]:
        st.caption("BBox preview cut from original page")
        preview = _crop_preview(page, bbox)
        if preview is not None:
            st.image(preview, use_container_width=True)
        else:
            st.info("No bbox preview can be generated from the original page.")


def _render_predictions(store: SoloReviewStore, item: dict[str, Any]) -> None:
    st.caption("Model predictions and model redraws")
    columns = st.columns(3)
    for column, backend in zip(columns, ("molscribe", "decimer", "ensemble")):
        with column:
            st.caption(backend.title())
            smiles = str(item.get(f"{backend}_smiles") or "")
            st.code(smiles or "No valid SMILES", language=None)
            if item.get("trusted_ground_truth_available"):
                st.caption("Matches trusted ground truth: " + ("yes" if item.get(f"{backend}_matches_ground_truth") else "no"))
            redraw = store.prediction_redraw(str(item.get("sample_id") or "sample"), backend, smiles)
            if redraw:
                st.image(redraw, caption="Model redraw, not the original image", use_container_width=True)
            else:
                st.info("No redraw")
            with st.expander("Raw prediction", expanded=False):
                st.json(_parse_json(item.get(f"{backend}_raw"), {"smiles": smiles}))


def _render_details(item: dict[str, Any]) -> None:
    details = st.columns(3)
    with details[0]:
        st.caption("Risks")
        st.json(_parse_json(item.get("risk_reasons"), []))
    with details[1]:
        st.caption("Deterministic checks")
        st.json(_parse_json(item.get("deterministic_errors"), []))
        failures = _model_failures(item)
        if failures:
            st.caption("Model failures")
            st.json(failures)
    with details[2]:
        st.caption("Source and license")
        st.json({
            "source_document": item.get("source_document"), "source_url": item.get("source_url"),
            "source_license": item.get("source_license"), "attribution": item.get("attribution"),
            "bbox": _parse_json(item.get("bbox"), []),
        })


def _render_actions(store: SoloReviewStore, item: dict[str, Any], *, recheck: bool) -> None:
    sample_id = str(item.get("sample_id") or "")
    st.divider()
    st.subheader("Visual Review")
    bbox_before = _parse_json(item.get("bbox"), [])
    bbox_values = bbox_before if len(bbox_before) == 4 else [0, 0, 0, 0]
    suggested = "missing_source_file" if not item.get("files_complete") else _current_visual_status(item)
    prefix = "recheck_" if recheck else ""
    visual_key = f"visual_status_{prefix}{sample_id}"
    region_key = f"solo_region_{prefix}{sample_id}"
    reviewer_key = f"solo_reviewer_{prefix}{sample_id}"
    notes_key = f"solo_notes_{prefix}{sample_id}"
    bbox_keys = [f"solo_bbox_{prefix}{sample_id}_{label}" for label in ("x1", "y1", "x2", "y2")]
    with st.form(key=f"visual_review_form_{prefix}{sample_id}", border=True):
        st.selectbox("Visual result", VISUAL_REVIEW_STATUSES, index=VISUAL_REVIEW_STATUSES.index(suggested), key=visual_key)
        editor = st.columns([0.24, 0.20, 0.56])
        editor[0].selectbox("Region type", REGION_TYPES, index=_region_index(item), key=region_key)
        editor[1].text_input("Reviewer", value="local", disabled=recheck, key=reviewer_key)
        editor[2].text_input("Review notes", key=notes_key)
        bbox_columns = st.columns(4)
        for index, (label, key) in enumerate(zip(("x1", "y1", "x2", "y2"), bbox_keys)):
            bbox_columns[index].number_input(label, min_value=0, value=int(bbox_values[index]), step=1, key=key)
        st.form_submit_button(
            "Save visual review",
            type="primary",
            on_click=_save_visual_review,
            args=(store, sample_id, recheck, visual_key, region_key, reviewer_key, notes_key, bbox_keys),
        )
    reviewer = str(st.session_state.get(reviewer_key) or "local")
    notes = str(st.session_state.get(notes_key) or "")
    if recheck:
        return
    st.divider()
    st.subheader("Structure Ground Truth Review")
    _render_ground_truth_confirmation(store, item, reviewer, notes)


def _save_visual_review(
    store: SoloReviewStore,
    sample_id: str,
    recheck: bool,
    visual_key: str,
    region_key: str,
    reviewer_key: str,
    notes_key: str,
    bbox_keys: list[str],
) -> None:
    """Persist form state before Streamlit performs its single submit rerun."""
    try:
        visual_status = str(st.session_state[visual_key])
        region_type = str(st.session_state[region_key])
        reviewer = str(st.session_state.get(reviewer_key) or "local")
        notes = str(st.session_state.get(notes_key) or "")
        bbox_after = [int(st.session_state[key]) for key in bbox_keys]
        if recheck:
            store.submit_recheck(
                sample_id,
                visual_review_status=visual_status,
                bbox_after=bbox_after,
                region_type=region_type,
                review_notes=notes,
            )
        else:
            store.submit_visual(
                sample_id,
                visual_review_status=visual_status,
                bbox_after=bbox_after,
                region_type=region_type,
                review_notes=notes,
                reviewer=reviewer,
            )
        st.session_state["solo_review_notice"] = f"Saved visual review for {sample_id}."
        st.session_state.pop("solo_review_error", None)
    except Exception as exc:
        st.session_state["solo_review_error"] = str(exc)


def _render_ground_truth_confirmation(store: SoloReviewStore, item: dict[str, Any], reviewer: str, notes: str) -> None:
    if not item.get("trusted_ground_truth_available"):
        st.info("没有可信结构真值，不可用于 OCSR 准确率评测。该样本只能完成 Visual Review，并可进入 detector/rejector 数据集。")
        return
    st.json({
        "ground_truth_origin": item.get("ground_truth_origin"), "ground_truth_smiles": item.get("ground_truth_smiles"),
        "ground_truth_inchikey": item.get("ground_truth_inchikey"), "source_compound_id": item.get("source_compound_id") or item.get("source_id"),
        "source_structure_file": item.get("source_structure_file"), "source_document": item.get("source_document"),
        "molscribe_matches_ground_truth": item.get("molscribe_matches_ground_truth"),
        "decimer_matches_ground_truth": item.get("decimer_matches_ground_truth"),
        "ensemble_matches_ground_truth": item.get("ensemble_matches_ground_truth"),
    })
    visual_complete = item.get("audit", {}).get("visual_review_status") == "valid_single_molecule_crop"
    disabled = not item.get("files_complete") or not visual_complete
    if not visual_complete:
        st.info("Save Visual Review as valid_single_molecule_crop before confirming the trusted structure label.")
    st.button(
        "Accept trusted ground truth",
        disabled=disabled,
        key=f"accept_truth_{item.get('sample_id')}",
        on_click=_accept_ground_truth,
        args=(store, str(item["sample_id"]), reviewer, notes),
    )


def _accept_ground_truth(store: SoloReviewStore, sample_id: str, reviewer: str, notes: str) -> None:
    """Confirm trusted truth in the button callback to avoid a second rerun."""
    try:
        store.submit_structure_ground_truth(sample_id, reviewer=reviewer, review_notes=notes)
        st.session_state["solo_review_notice"] = f"Accepted trusted ground truth for {sample_id}."
        st.session_state.pop("solo_review_error", None)
    except Exception as exc:
        st.session_state["solo_review_error"] = str(exc)


def _page_with_bbox(path: Path, bbox: list[int]) -> Image.Image:
    with Image.open(path) as image:
        preview = image.convert("RGB")
    if len(bbox) == 4:
        ImageDraw.Draw(preview).rectangle(tuple(bbox), outline="#d94841", width=max(2, preview.width // 300))
    return preview


def _crop_preview(page_path: str | None, bbox: list[int]) -> Image.Image | None:
    if not page_path or len(bbox) != 4:
        return None
    try:
        with Image.open(page_path) as image:
            x1, y1, x2, y2 = bbox
            if min(x1, y1) < 0 or x2 <= x1 or y2 <= y1 or x2 > image.width or y2 > image.height:
                return None
            return image.crop((x1, y1, x2, y2)).convert("RGB")
    except Exception:
        return None


def _current_visual_status(item: dict[str, Any]) -> str:
    value = str(item.get("audit", {}).get("visual_review_status") or "uncertain_visual")
    return value if value in VISUAL_REVIEW_STATUSES else "uncertain_visual"


def _region_index(item: dict[str, Any]) -> int:
    value = str(item.get("audit", {}).get("region_type_after") or item.get("machine_category") or item.get("category") or "invalid_crop")
    return REGION_TYPES.index(value) if value in REGION_TYPES else REGION_TYPES.index("invalid_crop")


def _parse_json(raw: str | None, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return default


def _model_failures(item: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for backend in ("molscribe", "decimer", "ensemble"):
        raw = _parse_json(item.get(f"{backend}_raw"), {})
        if not isinstance(raw, dict):
            continue
        if str(raw.get("status") or "").lower() not in {"", "success"} or raw.get("message"):
            failures.append({"backend": backend, "status": raw.get("status"), "message": raw.get("message")})
    return failures
