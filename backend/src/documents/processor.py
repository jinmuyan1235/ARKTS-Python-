"""Document-level OCSR pipeline: pages, regions, crops, recognition, exports."""

from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4
import shutil
import zipfile

import cv2
import numpy as np
from PIL import Image, ImageDraw

import config
from src.analysis.batch_analyzer import flatten_report
from src.analysis.correction import apply_smiles_correction, confirm_structure, revoke_structure_confirmation
from src.analysis.molecule_report import MoleculeReportGenerator
from src.documents.detectors import (
    BaseMoleculeRegionDetector,
    DocumentRegionStreams,
    HeuristicMoleculeRegionDetector,
    HybridMoleculeRegionDetector,
    SplitDocumentRegionDetector,
)
from src.documents.candidate_screening import (
    CandidateProposalConfig, CandidateScreeningConfig, CropScreeningConfig, assess_output_complexity,
    get_crop_screening_config, get_proposal_config, screen_region_candidate,
)
from src.documents.input_loader import DocumentInputLoader
from src.documents.models import DocumentPage, DocumentRegion, normalize_bbox, relative_path
from src.documents.region_editing import apply_region_edits, is_region_confirmed, summarize_regions
from src.export.csv_exporter import save_csv
from src.export.json_exporter import save_json
from src.export.structure_exporter import export_batch_structure_files
from src.feedback.store import export_document_detection_annotations, save_review_queue_item
from src.utils.file_utils import ensure_directory


REACTION_REGION_TYPES = {"reaction", "reaction_like", "reaction_arrow", "reaction_condition"}


class DocumentOCSRProcessor:
    """Run document rendering, region detection, region OCSR, and export."""

    def __init__(
        self,
        backend: str | None = None,
        output_dir: str | Path = config.DOCUMENT_OUTPUT_DIR,
        detector: BaseMoleculeRegionDetector | None = None,
        loader: DocumentInputLoader | None = None,
        runtime_config: dict[str, Any] | None = None,
        review_output_dir: str | Path | None = None,
        proposal_config: str | CandidateProposalConfig = "baseline",
        document_layout_proposal_config: str | CandidateProposalConfig = "baseline",
        crop_screening_config: str | CropScreeningConfig = "candidate",
        screening_config: str | CandidateScreeningConfig | None = None,
    ) -> None:
        self.output_dir = ensure_directory(output_dir)
        self.backend = backend
        if screening_config is not None:
            import warnings
            warnings.warn(
                "screening_config is deprecated; use proposal_config and crop_screening_config",
                DeprecationWarning, stacklevel=2,
            )
            legacy_name = screening_config.name if isinstance(screening_config, CandidateScreeningConfig) else screening_config
            proposal_config = legacy_name
            crop_screening_config = legacy_name
        self.proposal_config = get_proposal_config(proposal_config)
        self.document_layout_proposal_config = get_proposal_config(document_layout_proposal_config)
        self.crop_screening_config = get_crop_screening_config(crop_screening_config)
        self.detector = detector or SplitDocumentRegionDetector(
            molecule_detector=HybridMoleculeRegionDetector(
                fallback=HeuristicMoleculeRegionDetector(
                    proposal_config=self.proposal_config,
                    crop_screening_config=self.crop_screening_config,
                ),
            ),
            layout_detector=HybridMoleculeRegionDetector(
                fallback=HeuristicMoleculeRegionDetector(
                    proposal_config=self.document_layout_proposal_config,
                    crop_screening_config=self.crop_screening_config,
                ),
            ),
        )
        self.loader = loader or DocumentInputLoader(self.output_dir)
        self.report_generator = MoleculeReportGenerator(
            backend=backend,
            output_dir=self.output_dir,
            runtime_config=runtime_config,
        )
        self.review_output_dir = Path(review_output_dir).expanduser().resolve() if review_output_dir else None

    def process(
        self,
        input_path: str | Path,
        run_ocsr: bool = True,
        progress_callback: Callable[[int, int, str], None] | None = None,
        document_progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        """Process a PDF, page image, or ZIP image collection."""
        started = perf_counter()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
        document_id, pages = self.loader.load(input_path)
        if document_progress_callback is not None:
            document_progress_callback("rendered", len(pages), len(pages), f"已完成 {len(pages)} 页渲染。")
        document_dir = ensure_directory(self.output_dir / f"{document_id}_{run_id}")
        pages = self._move_pages_into_run(pages, document_dir)
        regions: list[DocumentRegion] = []
        detection_errors: list[dict[str, Any]] = []
        stream_counts = {"molecule_extraction": 0, "document_layout": 0}
        for page_index, page in enumerate(pages, start=1):
            try:
                streams = self.detect_page_streams(page)
                stream_counts["molecule_extraction"] += len(streams.molecule_extraction)
                stream_counts["document_layout"] += len(streams.document_layout)
                detected = streams.combined(page)
                if len(regions) + len(detected) > config.DOCUMENT_MAX_REGIONS:
                    allowed = max(config.DOCUMENT_MAX_REGIONS - len(regions), 0)
                    detected = detected[:allowed]
                    detection_errors.append({
                        "page_number": page.page_number,
                        "message": "已达到最大区域数量限制，剩余检测结果已跳过。",
                    })
                regions.extend(detected)
            except Exception as exc:
                detection_errors.append({"page_number": page.page_number, "message": str(exc)})
            if document_progress_callback is not None:
                document_progress_callback(
                    "detecting",
                    page_index,
                    len(pages),
                    f"正在检测第 {page_index}/{len(pages)} 页。",
                )
        recognized_candidates = 0
        skipped_candidates = 0
        if run_ocsr:
            candidates: list[DocumentRegion] = []
            for region in regions:
                if region.region_type == "molecule" and not is_region_confirmed(region.to_dict()):
                    region.status = "detected"
                    region.message = "等待人工确认后识别。"
                    continue
                if self.prepare_region_for_ocsr(region, pages):
                    candidates.append(region)
                elif region.region_type == "molecule":
                    skipped_candidates += 1
            total = len(candidates)
            for index, region in enumerate(candidates, start=1):
                if progress_callback is not None:
                    progress_callback(index, total, region.region_id)
                if document_progress_callback is not None:
                    document_progress_callback("recognizing", index, total, region.region_id)
                self.recognize_region(region, pages, document_dir, screen=False)
                if region.status == "recognized":
                    recognized_candidates += 1
        result = self._result(input_path, document_id, document_dir, pages, regions, detection_errors)
        result["processing"] = {
            "mode": "detect_and_recognize" if run_ocsr else "detect_only",
            "workflow": "full_document_review",
            "processed_page_count": len(pages),
            "total_time_ms": round((perf_counter() - started) * 1000, 2),
            "recognized_candidate_count": recognized_candidates,
            "skipped_candidate_count": skipped_candidates,
            "candidate_region_count": len([region for region in regions if region.region_type == "molecule"]),
            "confirmed_candidate_region_count": len([
                region for region in regions
                if region.region_type == "molecule" and is_region_confirmed(region.to_dict())
            ]),
            "region_stream_counts": stream_counts,
        }
        result["exports"] = self.export(result, document_dir)
        return result

    def detect_page_streams(self, page: DocumentPage) -> DocumentRegionStreams:
        """Return explicit molecule and layout streams without either overwriting the other."""

        detect_streams = getattr(self.detector, "detect_streams", None)
        if callable(detect_streams):
            streams = detect_streams(page)
            detected = streams.combined(page)
        else:
            detected = self.detector.detect(page)
        detected = self._apply_page_context_screening(page, detected)
        molecules = [region for region in detected if region.region_type == "molecule"]
        layout = [region for region in detected if region.region_type != "molecule"]
        for region in molecules:
            region.source = region.source or "molecule_extraction"
        for region in layout:
            region.source = region.source or "document_layout"
        return DocumentRegionStreams(molecules, layout, original_order=detected)

    def _apply_page_context_screening(
        self,
        page: DocumentPage,
        regions: list[DocumentRegion],
    ) -> list[DocumentRegion]:
        """Apply visual and PDF-layer gates consistently to every molecule proposal."""

        if not regions:
            return regions
        image = cv2.imdecode(np.fromfile(str(Path(page.image_path)), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return regions
        page_area = max(page.width * page.height, 1)
        figure_boxes = list(page.figure_boxes)
        figure_boxes.extend(
            {"bbox": list(region.bbox), "source": "layout_region"}
            for region in regions
            if region.region_type in {"figure", "table"}
            and ((region.bbox[2] - region.bbox[0]) * (region.bbox[3] - region.bbox[1])) / page_area >= 0.025
        )
        for region in regions:
            if region.region_type not in {"molecule", "unknown", "uncertain_visual"}:
                continue
            screening = screen_region_candidate(
                image,
                region.bbox,
                region.region_type,
                region.detection_confidence,
                config=self.crop_screening_config,
                text_boxes=page.text_boxes,
                figure_boxes=figure_boxes,
            )
            recommended = screening.recommended_region_type
            region.region_type = "reaction_like" if recommended == "reaction" else (
                "unknown" if recommended == "uncertain" else recommended
            )
            region.screening = screening.to_dict()
            region.detection_confidence = round(screening.screening_score, 3)
            region.message = ", ".join(screening.reason_codes)
        return regions

    def rescreen_document_result(self, document_result: dict[str, Any]) -> dict[str, Any]:
        """Reapply the latest non-OCSR gates to unconfirmed automatic regions."""

        updated = deepcopy(document_result)
        regions = updated.get("regions") or []
        changed = 0
        checked = 0
        errors: list[dict[str, Any]] = []
        for page in updated.get("pages") or []:
            page_number = int(page.get("page_number") or 0)
            page_regions = [
                region
                for region in regions
                if int(region.get("page_number") or 0) == page_number and region.get("status") != "deleted"
            ]
            eligible = [
                region
                for region in page_regions
                if str(region.get("source") or "detector") != "user"
                and not bool(region.get("confirmed"))
                and str(region.get("status") or "") != "recognized"
            ]
            if not eligible:
                continue
            image_path = Path(str(page.get("image_path") or ""))
            image = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                errors.append({"page_number": page_number, "message": "页面图片无法读取，未重新筛选。"})
                continue
            page_area = max(int(page.get("width") or 1) * int(page.get("height") or 1), 1)
            figure_boxes = list(page.get("figure_boxes") or [])
            figure_boxes.extend(
                {"bbox": list(region.get("bbox") or []), "source": "layout_region"}
                for region in page_regions
                if region.get("region_type") in {"figure", "table"}
                and len(region.get("bbox") or []) == 4
                and (
                    (int(region["bbox"][2]) - int(region["bbox"][0]))
                    * (int(region["bbox"][3]) - int(region["bbox"][1]))
                ) / page_area >= 0.025
            )
            for region in eligible:
                try:
                    result = screen_region_candidate(
                        image,
                        region.get("bbox") or [],
                        str(region.get("region_type") or "unknown"),
                        region.get("detection_confidence"),
                        config=self.crop_screening_config,
                        text_boxes=page.get("text_boxes") or [],
                        figure_boxes=figure_boxes,
                    )
                except (TypeError, ValueError) as exc:
                    errors.append({"page_number": page_number, "region_id": region.get("region_id"), "message": str(exc)})
                    continue
                checked += 1
                before_type = str(region.get("region_type") or "unknown")
                recommended = result.recommended_region_type
                after_type = "reaction_like" if recommended == "reaction" else (
                    "unknown" if recommended == "uncertain" else recommended
                )
                region["region_type"] = after_type
                region["screening"] = result.to_dict()
                region["detection_confidence"] = round(result.screening_score, 3)
                region["message"] = ", ".join(result.reason_codes)
                if before_type != after_type:
                    changed += 1
                    region.setdefault("audit", []).append({
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "operation": "automatic_rescreen",
                        "before": {"region_type": before_type},
                        "after": {"region_type": after_type},
                        "note": "Applied latest PDF candidate-screening rules without OCSR.",
                    })
        updated["summary"] = self._summary(updated.get("pages", []), regions, updated.get("detection_errors", []))
        updated.setdefault("processing", {})["screening_refresh"] = {
            "config_version": f"crop-screening-{self.crop_screening_config.name}-v3",
            "checked_region_count": checked,
            "changed_region_count": changed,
            "errors": errors,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        output_dir = Path(str(updated.get("output_dir") or self.output_dir))
        updated["exports"] = self.export(updated, output_dir)
        return updated

    def recognize_region(
        self,
        region: DocumentRegion | dict[str, Any],
        pages: list[DocumentPage] | list[dict[str, Any]],
        document_dir: str | Path,
        screen: bool = True,
    ) -> DocumentRegion | dict[str, Any]:
        """Crop and recognize one molecule region, leaving non-molecule regions untouched."""
        if isinstance(region, dict):
            region_obj = DocumentRegion(
                document_id=region["document_id"],
                page_number=int(region["page_number"]),
                region_id=region["region_id"],
                bbox=tuple(region["bbox"]),
                region_type=region.get("region_type", "unknown"),
                detection_confidence=region.get("detection_confidence"),
                crop_path=region.get("crop_path"),
                source=region.get("source", "detector"),
                detector_name=region.get("detector_name"),
                status=region.get("status", "detected"),
                confirmed=bool(region.get("confirmed")),
                message=region.get("message"),
                audit=region.get("audit", []),
                ocsr=region.get("ocsr", {}),
                final_result=region.get("final_result", {}),
                report=region.get("report"),
                screening=region.get("screening", {}),
                review=region.get("review", {}),
                processing_time_ms=region.get("processing_time_ms"),
            )
            self.recognize_region(region_obj, pages, document_dir, screen=screen)
            region.update(region_obj.to_dict())
            return region

        if region.status == "deleted":
            return region
        if screen and not self.prepare_region_for_ocsr(region, pages):
            return region
        page = self._find_page(pages, region.page_number)
        region_started = perf_counter()
        crop_path: Path | None = None
        try:
            crop_path = self.crop_region(page, region, document_dir)
            region.crop_path = str(crop_path.resolve())
            report = self.report_generator.generate(image_path=crop_path)
            report.setdefault("document_region", {})
            report["document_region"].update({
                "document_id": region.document_id,
                "page_number": region.page_number,
                "region_id": region.region_id,
                "bbox": list(region.bbox),
                "region_type": region.region_type,
                "detection_confidence": region.detection_confidence,
                "confirmed": region.confirmed,
            })
            report.setdefault("analysis_id", f"{region.document_id}_{region.region_id}")
            report.setdefault("input", {})
            report["input"]["document_id"] = region.document_id
            report["input"]["page_number"] = region.page_number
            report["input"]["region_id"] = region.region_id
            report["input"]["bbox"] = list(region.bbox)
            report["input"]["type"] = "document_region"
            output_smiles = (
                (report.get("final") or {}).get("canonical_smiles")
                or (report.get("final") or {}).get("smiles")
                or (report.get("ocsr") or {}).get("smiles")
            )
            screening_diagnostics = (region.screening or {}).get("diagnostics") or region.screening
            complexity_gate = assess_output_complexity(screening_diagnostics, output_smiles)
            report.setdefault("recognition_gate", {})["input_output_complexity"] = complexity_gate
            if not complexity_gate.get("passed", True):
                report["rejected_candidate"] = report.get("final") or {}
                report["final"] = {}
                report["status"] = "failed"
                report["message"] = "模型输出复杂度与输入图形明显不一致，已拒绝作为有效识别结果。"
            region.report = report
            region.ocsr = report.get("ocsr") or {}
            region.final_result = report.get("final") or {}
            region.status = "recognized" if report.get("status") == "success" else "failed"
            region.message = report.get("message") or ("识别成功。" if region.status == "recognized" else "识别失败。")
            region.processing_time_ms = round((perf_counter() - region_started) * 1000, 2)
            if region.status == "failed":
                self._queue_failed_region_for_review(region)
        except Exception as exc:
            region.status = "failed"
            region.message = f"区域识别失败：{exc}"
            region.processing_time_ms = round((perf_counter() - region_started) * 1000, 2)
            region.report = {
                "analysis_id": f"{region.document_id}_{region.region_id}",
                "status": "failed",
                "message": region.message,
                "input": {
                    "type": "document_region",
                    "document_id": region.document_id,
                    "page_number": region.page_number,
                    "region_id": region.region_id,
                    "bbox": list(region.bbox),
                    "path": str(crop_path) if crop_path is not None else None,
                },
                "document_region": {
                    "document_id": region.document_id,
                    "page_number": region.page_number,
                    "region_id": region.region_id,
                    "bbox": list(region.bbox),
                    "region_type": region.region_type,
                    "detection_confidence": region.detection_confidence,
                    "confirmed": region.confirmed,
                },
            }
            self._queue_failed_region_for_review(region)
        return region

    def prepare_region_for_ocsr(
        self,
        region: DocumentRegion,
        pages: list[DocumentPage] | list[dict[str, Any]],
    ) -> bool:
        """Run inexpensive safety and text filters before sending a crop to OCSR."""
        if region.status == "deleted":
            return False
        if region.region_type != "molecule":
            detector_message = region.message
            region.status = "skipped"
            region.message = self._non_molecule_skip_message(region.region_type)
            region.screening = {"passed": False, "reason": region.message, "detector_message": detector_message}
            return False
        if not is_region_confirmed(region.to_dict()):
            region.status = "detected"
            region.message = "等待人工确认后识别。"
            region.screening = {"passed": False, "reason": region.message, "requires_confirmation": True}
            return False
        try:
            page = self._find_page(pages, region.page_number)
            passed, screening = self._shared_screen_region_candidate(page, region)
        except Exception as exc:
            region.status = "skipped"
            region.message = f"区域检查失败，已跳过识别：{exc}"
            region.screening = {"passed": False, "reason": region.message}
            return False
        region.screening = screening
        if not passed:
            region.status = "skipped"
            region.message = str(screening.get("reason") or "未通过分子区域二次筛选，已跳过识别。")
            return False
        region.status = "detected"
        region.message = str(screening.get("reason") or "通过分子区域二次筛选。")
        return True

    @staticmethod
    def _non_molecule_skip_message(region_type: str) -> str:
        if region_type in REACTION_REGION_TYPES:
            return "该区域属于反应式、箭头或反应条件，已分流，不作为单分子识别；请人工框选单个底物/产物分子，或进入反应解析流程。"
        if region_type == "table":
            return "该区域是表格，已跳过单分子识别。"
        if region_type in {"text", "figure_label"}:
            return "该区域是文本，已跳过单分子识别。"
        if region_type == "figure":
            return "该区域像普通图像/插图，已跳过单分子识别，建议人工确认。"
        if region_type == "ignore":
            return "该区域已标记为忽略，已跳过单分子识别。"
        return "该区域不是单个分子结构，已跳过单分子识别。"

    def _screen_region_candidate(
        self,
        page: DocumentPage | dict[str, Any],
        region: DocumentRegion,
    ) -> tuple[bool, dict[str, Any]]:
        page_path = Path(page.image_path if isinstance(page, DocumentPage) else page["image_path"])
        page_width = int(page.width if isinstance(page, DocumentPage) else page["width"])
        page_height = int(page.height if isinstance(page, DocumentPage) else page["height"])
        bbox = normalize_bbox(region.bbox, page_width, page_height)
        region.bbox = bbox
        x1, y1, x2, y2 = bbox
        width, height = x2 - x1, y2 - y1
        if width < 70 or height < 55:
            return False, {
                "passed": False,
                "reason": "区域尺寸过小，已跳过识别。",
                "width": width,
                "height": height,
            }
        image = cv2.imdecode(np.fromfile(str(page_path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return False, {"passed": False, "reason": "页面图片无法读取，已跳过识别。"}
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return False, {"passed": False, "reason": "裁剪区域为空，已跳过识别。", "width": width, "height": height}
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        foreground = gray < 245
        ink_ratio = float(np.mean(foreground))
        aspect = width / max(height, 1)
        heuristic_detector = self._heuristic_detector()
        binary = heuristic_detector._foreground_binary(crop) if heuristic_detector is not None else None
        component_count = 0
        significant_components = 0
        small_component_ratio = 0.0
        text_line_count = 0
        long_line_count = 0
        horizontal_projection = 0.0
        vertical_projection = 0.0
        if binary is not None:
            count, _, stats, _ = cv2.connectedComponentsWithStats((binary > 0).astype(np.uint8), 8)
            component_count = max(count - 1, 0)
            component_areas = [int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, count)]
            significant_components = sum(1 for area in component_areas if area >= 6)
            small_component_ratio = HeuristicMoleculeRegionDetector._small_component_ratio(component_areas)
            text_line_count = HeuristicMoleculeRegionDetector._text_line_count(binary)
            _, long_line_count = HeuristicMoleculeRegionDetector._line_segment_counts(binary)
            horizontal_projection = float(np.max(np.sum(binary > 0, axis=1)) / max(width, 1))
            vertical_projection = float(np.max(np.sum(binary > 0, axis=0)) / max(height, 1))
        skeletal_linework = (
            long_line_count >= 8
            and significant_components <= 55
            and ink_ratio < 0.12
            and small_component_ratio < 0.35
            and horizontal_projection < 0.55
            and vertical_projection < 0.55
        )
        base = {
            "passed": True,
            "width": width,
            "height": height,
            "aspect": round(aspect, 3),
            "ink_ratio": round(ink_ratio, 5),
            "component_count": component_count,
            "significant_component_count": significant_components,
            "small_component_ratio": round(small_component_ratio, 3),
            "text_line_count": text_line_count,
            "long_line_count": long_line_count,
            "skeletal_linework": skeletal_linework,
        }
        if ink_ratio < 0.006:
            base.update({"passed": False, "reason": "区域前景过少，疑似空白，已跳过识别。"})
            return False, base
        if ink_ratio > 0.38:
            base.update({"passed": False, "reason": "区域前景过密，疑似正文或表格，已跳过识别。"})
            return False, base
        if binary is not None and HeuristicMoleculeRegionDetector._looks_like_table(binary, aspect, horizontal_projection, vertical_projection):
            base.update({"passed": False, "reason": "区域呈表格网格形态，已跳过单分子识别。"})
            return False, base
        if binary is not None and HeuristicMoleculeRegionDetector._looks_like_reaction_arrow(binary, aspect, width, height, ink_ratio):
            base.update({"passed": False, "reason": "区域疑似反应箭头或反应式，已分流，不作为单分子识别。"})
            return False, base
        if binary is not None and HeuristicMoleculeRegionDetector._looks_like_reaction_condition(
            width,
            height,
            aspect,
            ink_ratio,
            [1] * significant_components,
            text_line_count,
            small_component_ratio,
        ):
            base.update({"passed": False, "reason": "区域疑似反应条件标签，已跳过单分子识别。"})
            return False, base
        if (height < 90 and aspect > 1.8 and significant_components >= 2) or (aspect > 4.5 and height < 150):
            base.update({"passed": False, "reason": "区域形态像单行文字标签，已跳过识别。"})
            return False, base
        if not skeletal_linework and text_line_count >= 5 and significant_components >= 22 and ink_ratio < 0.30:
            base.update({"passed": False, "reason": "区域疑似多行正文，已跳过识别。"})
            return False, base
        if not skeletal_linework and text_line_count >= 4 and significant_components >= 18 and aspect > 0.75 and ink_ratio < 0.30:
            base.update({"passed": False, "reason": "区域文字密度较高，已跳过识别。"})
            return False, base
        if significant_components >= 35 and small_component_ratio > 0.72 and ink_ratio < 0.26:
            base.update({"passed": False, "reason": "区域由大量小字符连通组件组成，已跳过识别。"})
            return False, base
        if significant_components >= 12 and aspect > 1.4 and height < 240 and ink_ratio < 0.22:
            base.update({"passed": False, "reason": "区域疑似正文文本，已跳过识别。"})
            return False, base
        base["reason"] = "通过分子区域二次筛选。"
        return True, base

    def _shared_screen_region_candidate(
        self,
        page: DocumentPage | dict[str, Any],
        region: DocumentRegion,
    ) -> tuple[bool, dict[str, Any]]:
        """Use the same public visual gate as offline collection and evaluation."""
        page_path = Path(page.image_path if isinstance(page, DocumentPage) else page["image_path"])
        page_width = int(page.width if isinstance(page, DocumentPage) else page["width"])
        page_height = int(page.height if isinstance(page, DocumentPage) else page["height"])
        bbox = normalize_bbox(region.bbox, page_width, page_height)
        region.bbox = bbox
        result = screen_region_candidate(
            page_path,
            bbox,
            region.region_type,
            region.detection_confidence,
            config=self.crop_screening_config,
            text_boxes=(page.text_boxes if isinstance(page, DocumentPage) else page.get("text_boxes") or []),
            figure_boxes=(page.figure_boxes if isinstance(page, DocumentPage) else page.get("figure_boxes") or []),
        )
        payload = result.to_dict()
        payload["passed"] = result.decision == "accept_molecule"
        payload["requires_review"] = result.decision == "review_needed"
        if "too_small" in result.reason_codes:
            payload["reason"] = "区域尺寸过小，已跳过识别。"
        elif "text_like" in result.reason_codes:
            payload["reason"] = "区域疑似文字，已跳过单分子识别。"
        else:
            payload["reason"] = ", ".join(result.reason_codes)
        payload.update(result.diagnostics)
        return result.decision == "accept_molecule", payload

    def _heuristic_detector(self) -> HeuristicMoleculeRegionDetector | None:
        if isinstance(self.detector, HeuristicMoleculeRegionDetector):
            return self.detector
        fallback = getattr(self.detector, "fallback", None)
        if isinstance(fallback, HeuristicMoleculeRegionDetector):
            return fallback
        return None

    def apply_edits(
        self,
        document_result: dict[str, Any],
        edits: list[dict[str, Any]],
        rerun_ocsr: bool = False,
        progress_callback: Callable[[str, int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        """Apply human bbox/type edits and optionally re-run OCSR on edited molecule regions."""
        before_ids = {str(region.get("region_id")) for region in document_result.get("regions", [])}
        report_actions = {"correct_smiles", "confirm_structure", "revoke_structure_confirmation"}
        region_edits = [edit for edit in edits if str(edit.get("action") or "").lower() not in report_actions]
        updated = apply_region_edits(document_result, region_edits) if region_edits else deepcopy(document_result)
        document_dir = Path(updated["output_dir"])
        for edit in edits:
            action = str(edit.get("action") or "").lower()
            if action not in report_actions:
                continue
            region_id = str(edit.get("region_id") or "")
            region = next(
                (item for item in updated.get("regions", []) if str(item.get("region_id")) == region_id),
                None,
            )
            if region is None:
                raise ValueError(f"Unknown region_id for report review: {region_id}")
            report = region.get("report") or {}
            if not report:
                raise ValueError("当前区域尚无候选结构，请先执行识别。")
            previous_smiles = ((report.get("final") or {}).get("smiles") or (report.get("ocsr") or {}).get("smiles"))
            if action == "correct_smiles":
                report = apply_smiles_correction(report, str(edit.get("smiles") or ""), document_dir)
                error = ((report.get("correction") or {}).get("last_error") or (report.get("validation") or {}).get("error"))
                if error:
                    raise ValueError(str(error))
                region["confirmed"] = False
                region["annotation_status"] = "pending"
            elif action == "confirm_structure":
                report = confirm_structure(report)
                error = (report.get("human_review") or {}).get("last_error")
                if error:
                    raise ValueError(str(error))
                region["confirmed"] = True
                region["annotation_status"] = "confirmed"
            else:
                report = revoke_structure_confirmation(report)
                region["confirmed"] = False
                region["annotation_status"] = "pending"
            region["report"] = report
            region["ocsr"] = report.get("ocsr") or region.get("ocsr") or {}
            region["final_result"] = report.get("final") or {}
            region["status"] = "recognized" if report.get("status") == "success" else region.get("status")
            region["message"] = report.get("message") or region.get("message")
            region.setdefault("audit", []).append({
                "operation": action,
                "before": {"smiles": previous_smiles},
                "after": {"smiles": ((report.get("final") or {}).get("smiles")), "confirmed": bool(region.get("confirmed"))},
                "note": edit.get("note"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "user",
            })
        if rerun_ocsr:
            requested_ids = {
                str(edit.get("region_id"))
                for edit in edits
                if edit.get("region_id") is not None
            }
            requested_pages = {
                int(edit.get("page_number"))
                for edit in edits
                if str(edit.get("action") or "").lower() == "confirm_page" and edit.get("page_number") is not None
            }
            requested_ids.update(
                str(region.get("region_id"))
                for region in updated.get("regions", [])
                if str(region.get("region_id")) not in before_ids
            )
            recognition_targets = [
                region
                for region in updated.get("regions", [])
                if (
                    region.get("status") in {"confirmed", "edited", "detected", "failed"}
                    and region.get("region_type") == "molecule"
                    and is_region_confirmed(region)
                    and (
                        not requested_ids and not requested_pages
                        or str(region.get("region_id")) in requested_ids
                        or int(region.get("page_number", 0)) in requested_pages
                    )
                )
            ]
            total_targets = len(recognition_targets)
            for index, region in enumerate(recognition_targets, start=1):
                region_id = str(region.get("region_id") or "")
                if progress_callback is not None:
                    progress_callback("recognizing", index, total_targets, region_id)
                self.recognize_region(region, updated.get("pages", []), document_dir)
                if progress_callback is not None:
                    progress_callback("recognized", index, total_targets, region_id)
        updated["summary"] = self._summary(updated.get("pages", []), updated.get("regions", []), updated.get("detection_errors", []))
        updated["exports"] = self.export(updated, document_dir)
        return updated

    def crop_region(self, page: DocumentPage | dict[str, Any], region: DocumentRegion, document_dir: str | Path) -> Path:
        """Crop a region image while preserving page-coordinate bbox metadata."""
        page_path = Path(page.image_path if isinstance(page, DocumentPage) else page["image_path"])
        page_width = int(page.width if isinstance(page, DocumentPage) else page["width"])
        page_height = int(page.height if isinstance(page, DocumentPage) else page["height"])
        region.bbox = normalize_bbox(region.bbox, page_width, page_height)
        image = cv2.imdecode(np.fromfile(str(page_path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Unable to decode page image: {page_path}")
        x1, y1, x2, y2 = region.bbox
        crop = image[y1:y2, x1:x2]
        crop_dir = ensure_directory(Path(document_dir) / "crops")
        crop_path = crop_dir / f"{region.document_id}_{region.region_id}.png"
        cv2.imencode(".png", crop)[1].tofile(str(crop_path))
        return crop_path

    def export(self, document_result: dict[str, Any], document_dir: str | Path) -> dict[str, str]:
        """Export JSON, CSV, annotated pages, failures, crops, redrawn images, and a ZIP package."""
        output_root = ensure_directory(document_dir)
        rows = self.region_rows(document_result)
        csv_path = save_csv(rows, output_root / "regions.csv")
        failures = [row for row in rows if row.get("region_type") == "molecule" and row.get("status") not in {"recognized"}]
        failure_path = save_csv(failures, output_root / "failed_regions.csv")
        structure_reports: list[dict[str, Any]] = []
        structure_rows: list[dict[str, Any]] = []
        for region, row in zip(document_result.get("regions", []), rows):
            report = region.get("report") or {}
            if report:
                structure_reports.append(report)
                structure_rows.append(row)
        structure_exports = export_batch_structure_files(
            structure_reports,
            output_root / "structure_exports",
            structure_rows,
            file_prefix="document",
        )
        annotated_paths = self._save_annotated_pages(document_result, output_root)
        detection_annotations = export_document_detection_annotations(
            [document_result],
            output_root / "detection_annotations.json",
            root=output_root,
        )
        redrawn_dir = ensure_directory(output_root / "redrawn")
        for region in document_result.get("regions", []):
            redrawn = (((region.get("report") or {}).get("images") or {}).get("redrawn_molecule"))
            if redrawn and Path(redrawn).is_file():
                shutil.copy2(redrawn, redrawn_dir / f"{region.get('region_id')}_redrawn.png")
        exports = {
            "regions_csv": csv_path,
            "failure_cases_csv": failure_path,
            "annotated_pages_dir": str((output_root / "annotated_pages").resolve()),
            "annotated_pages": ",".join(annotated_paths),
            "crops_dir": str((output_root / "crops").resolve()),
            "redrawn_dir": str(redrawn_dir.resolve()),
            "structures_sdf": structure_exports["merged_sdf"],
            "structures_smi": structure_exports["merged_smi"],
            "structures_zip": structure_exports["successful_zip"],
            "structure_failed_csv": structure_exports["failed_csv"],
            "structure_review_csv": structure_exports["review_csv"],
            "detection_annotations_json": detection_annotations["output_path"],
        }
        document_result["exports"] = exports
        json_path = save_json(document_result, output_root / "document_result.json")
        exports["json"] = json_path
        zip_path = self._zip_outputs(output_root)
        exports["zip"] = str(zip_path.resolve())
        document_result["exports"] = exports
        save_json(document_result, output_root / "document_result.json")
        return exports

    @staticmethod
    def region_rows(document_result: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten document regions for CSV export."""
        rows: list[dict[str, Any]] = []
        output_dir = Path(document_result.get("output_dir", "."))
        for region in document_result.get("regions", []):
            report = region.get("report") or {}
            flat = flatten_report(report) if report else {}
            row = {
                "document_id": document_result.get("document_id"),
                "page_number": region.get("page_number"),
                "region_id": region.get("region_id"),
                "bbox_x1": (region.get("bbox") or [None, None, None, None])[0],
                "bbox_y1": (region.get("bbox") or [None, None, None, None])[1],
                "bbox_x2": (region.get("bbox") or [None, None, None, None])[2],
                "bbox_y2": (region.get("bbox") or [None, None, None, None])[3],
                "region_type": region.get("region_type"),
                "detection_confidence": region.get("detection_confidence"),
                "source": region.get("source"),
                "status": region.get("status"),
                "confirmed": bool(region.get("confirmed")),
                "annotation_status": region.get("annotation_status"),
                "message": region.get("message"),
                "crop_path": relative_path(region.get("crop_path"), output_dir) if region.get("crop_path") else None,
                "audit_count": len(region.get("audit") or []),
                "screening_passed": (region.get("screening") or {}).get("passed"),
                "screening_reason": (region.get("screening") or {}).get("reason"),
                "review_queued": (region.get("review") or {}).get("queued"),
                "review_annotation_path": (region.get("review") or {}).get("annotation_path"),
                "processing_time_ms": region.get("processing_time_ms"),
            }
            row.update({key: value for key, value in flat.items() if key not in row})
            rows.append(row)
        return rows

    def _result(
        self,
        input_path: str | Path,
        document_id: str,
        document_dir: Path,
        pages: list[DocumentPage],
        regions: list[DocumentRegion],
        detection_errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        region_dicts = [region.to_dict() for region in regions]
        page_dicts = [page.to_dict() for page in pages]
        return {
            "document_id": document_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_path": str(Path(input_path).expanduser().resolve()),
            "output_dir": str(document_dir.resolve()),
            "backend": self.report_generator.recognizer.backend,
            "detector": self.detector.name,
            "pages": page_dicts,
            "regions": region_dicts,
            "detection_errors": detection_errors,
            "summary": self._summary(page_dicts, region_dicts, detection_errors),
            "exports": {},
        }

    def _queue_failed_region_for_review(self, region: DocumentRegion) -> None:
        if self.review_output_dir is None or not is_region_confirmed(region.to_dict()):
            return
        if (region.review or {}).get("queued"):
            return
        report = region.report or {}
        if not report:
            return
        try:
            queued = save_review_queue_item(
                report,
                output_dir=self.review_output_dir,
                notes=f"Document region recognition failed: {region.document_id}/{region.region_id}",
                correction_type="other",
                source_reference=f"{region.document_id}:page-{region.page_number}:{region.region_id}",
                source_license="unspecified",
            )
            region.review = {
                "queued": True,
                "annotation_path": queued.get("annotation_path"),
                "manifest_path": queued.get("manifest_path"),
                "review_status": queued.get("review_status"),
            }
        except Exception as exc:
            region.review = {"queued": False, "error": str(exc)}

    @staticmethod
    def _summary(
        pages: list[DocumentPage] | list[dict[str, Any]],
        regions: list[DocumentRegion] | list[dict[str, Any]],
        detection_errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        region_dicts = [region.to_dict() if isinstance(region, DocumentRegion) else region for region in regions]
        summary = summarize_regions(region_dicts)
        summary["page_count"] = len(pages)
        summary["detection_error_count"] = len(detection_errors)
        return summary

    @staticmethod
    def _find_page(pages: list[DocumentPage] | list[dict[str, Any]], page_number: int) -> DocumentPage | dict[str, Any]:
        for page in pages:
            current = page.page_number if isinstance(page, DocumentPage) else int(page["page_number"])
            if current == page_number:
                return page
        raise ValueError(f"Page not found for region: {page_number}")

    @staticmethod
    def _move_pages_into_run(pages: list[DocumentPage], document_dir: Path) -> list[DocumentPage]:
        page_dir = ensure_directory(document_dir / "pages")
        moved: list[DocumentPage] = []
        for page in pages:
            source = Path(page.image_path)
            destination = page_dir / source.name
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            page.image_path = str(destination.resolve())
            moved.append(page)
        return moved

    @staticmethod
    def _save_annotated_pages(document_result: dict[str, Any], output_root: Path) -> list[str]:
        annotated_dir = ensure_directory(output_root / "annotated_pages")
        regions_by_page: dict[int, list[dict[str, Any]]] = {}
        for region in document_result.get("regions", []):
            if region.get("status") == "deleted":
                continue
            regions_by_page.setdefault(int(region.get("page_number", 0)), []).append(region)
        paths: list[str] = []
        colors = {
            "molecule": "green",
            "reaction_like": "orange",
            "reaction_arrow": "orange",
            "reaction_condition": "darkorange",
            "table": "blue",
            "figure": "teal",
            "text": "gray",
            "unknown": "purple",
            "non_molecule": "red",
        }
        for page in document_result.get("pages", []):
            source = Path(page["image_path"])
            with Image.open(source).convert("RGB") as image:
                draw = ImageDraw.Draw(image)
                for region in regions_by_page.get(int(page["page_number"]), []):
                    bbox = [int(value) for value in region.get("bbox", [0, 0, 0, 0])]
                    color = colors.get(region.get("region_type"), "purple")
                    draw.rectangle(bbox, outline=color, width=3)
                    draw.text((bbox[0] + 3, max(0, bbox[1] - 14)), str(region.get("region_id")), fill=color)
                destination = annotated_dir / f"{document_result.get('document_id')}_p{int(page['page_number']):03d}_annotated.png"
                image.save(destination)
                paths.append(str(destination.resolve()))
        return paths

    @staticmethod
    def _zip_outputs(output_root: Path) -> Path:
        zip_path = output_root / "document_results.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in output_root.rglob("*"):
                if item == zip_path or item.is_dir():
                    continue
                archive.write(item, item.relative_to(output_root))
        return zip_path
