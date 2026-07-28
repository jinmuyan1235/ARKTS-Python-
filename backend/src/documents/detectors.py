"""Molecule-region detection interfaces and lightweight OpenCV fallback."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import cv2
import numpy as np

import config
from src.documents.candidate_screening import (
    CandidateProposalConfig,
    CandidateScreeningConfig,
    CropScreeningConfig,
    get_crop_screening_config,
    get_proposal_config,
    screen_region_candidate,
)
from src.documents.models import DocumentPage, DocumentRegion, normalize_bbox


DetectorPrediction = DocumentRegion | dict[str, Any]


class BaseMoleculeRegionDetector(ABC):
    """Detector interface for future trainable molecule-region detectors."""

    name = "base"

    @abstractmethod
    def detect(self, page: DocumentPage) -> list[DocumentRegion]:
        """Return detected regions for one rendered page."""
        raise NotImplementedError


class TrainableMoleculeRegionDetector(BaseMoleculeRegionDetector):
    """Adapter for a trained layout detector when one is configured.

    The project does not ship a trained detector yet. This class keeps the
    document pipeline ready for one by normalizing model predictions into
    DocumentRegion objects while returning no regions when no predictor exists.
    """

    name = "trainable-layout"

    def __init__(
        self,
        predictor: Callable[[DocumentPage], Sequence[DetectorPrediction]] | None = None,
        name: str | None = None,
        min_confidence: float = 0.35,
    ) -> None:
        self.predictor = predictor
        if name:
            self.name = name
        self.min_confidence = min_confidence

    @property
    def available(self) -> bool:
        return self.predictor is not None

    def detect(self, page: DocumentPage) -> list[DocumentRegion]:
        if self.predictor is None:
            return []
        predictions = self.predictor(page)
        regions: list[DocumentRegion] = []
        for index, prediction in enumerate(predictions, start=1):
            region = self._coerce_prediction(page, prediction, index)
            if region is not None:
                regions.append(region)
        return regions

    def _coerce_prediction(
        self,
        page: DocumentPage,
        prediction: DetectorPrediction,
        index: int,
    ) -> DocumentRegion | None:
        if isinstance(prediction, DocumentRegion):
            confidence = prediction.detection_confidence
            if confidence is not None and confidence < self.min_confidence:
                return None
            try:
                bbox = normalize_bbox(prediction.bbox, page.width, page.height)
            except ValueError:
                return None
            prediction.document_id = page.document_id
            prediction.page_number = page.page_number
            prediction.region_id = prediction.region_id or f"p{page.page_number:03d}_t{index:03d}"
            prediction.bbox = bbox
            prediction.detector_name = prediction.detector_name or self.name
            return prediction

        confidence = prediction.get("detection_confidence", prediction.get("confidence", prediction.get("score")))
        if confidence is not None:
            confidence = float(confidence)
            if confidence < self.min_confidence:
                return None
        try:
            bbox = normalize_bbox(prediction["bbox"], page.width, page.height)
        except (KeyError, TypeError, ValueError):
            return None
        return DocumentRegion(
            document_id=page.document_id,
            page_number=page.page_number,
            region_id=str(prediction.get("region_id") or f"p{page.page_number:03d}_t{index:03d}"),
            bbox=bbox,
            region_type=str(prediction.get("region_type") or prediction.get("label") or "unknown"),
            detection_confidence=round(confidence, 3) if confidence is not None else None,
            detector_name=str(prediction.get("detector_name") or self.name),
            message=prediction.get("message"),
        )


class HybridMoleculeRegionDetector(BaseMoleculeRegionDetector):
    """Combine an optional trainable detector with the OpenCV fallback."""

    name = "hybrid-layout"

    def __init__(
        self,
        trainable: TrainableMoleculeRegionDetector | None = None,
        fallback: "HeuristicMoleculeRegionDetector | None" = None,
        overlap_threshold: float = 0.68,
    ) -> None:
        self.trainable = trainable or TrainableMoleculeRegionDetector()
        self.fallback = fallback or HeuristicMoleculeRegionDetector()
        self.overlap_threshold = overlap_threshold

    def detect(self, page: DocumentPage) -> list[DocumentRegion]:
        fallback_regions = self.fallback.detect(page)
        trainable_regions: list[DocumentRegion] = []
        if self.trainable.available:
            try:
                trainable_regions = self.trainable.detect(page)
            except Exception:
                trainable_regions = []
        if not trainable_regions:
            return self._renumber(page, fallback_regions)

        selected = list(trainable_regions)
        for region in fallback_regions:
            if not self._overlaps_selected(region, selected):
                selected.append(region)
        return self._renumber(page, sorted(selected, key=lambda item: (item.bbox[1], item.bbox[0])))

    def _overlaps_selected(self, region: DocumentRegion, selected: list[DocumentRegion]) -> bool:
        return any(
            HeuristicMoleculeRegionDetector._overlap_ratio(region.bbox, existing.bbox) >= self.overlap_threshold
            for existing in selected
        )

    def _renumber(self, page: DocumentPage, regions: list[DocumentRegion]) -> list[DocumentRegion]:
        for index, region in enumerate(regions, start=1):
            region.document_id = page.document_id
            region.page_number = page.page_number
            region.region_id = f"p{page.page_number:03d}_r{index:03d}"
        return regions


@dataclass
class DocumentRegionStreams:
    """Independent molecule-extraction and document-layout detector outputs."""

    molecule_extraction: list[DocumentRegion]
    document_layout: list[DocumentRegion]
    original_order: list[DocumentRegion] | None = None

    def combined(self, page: DocumentPage) -> list[DocumentRegion]:
        if self.original_order is not None:
            return list(self.original_order)
        regions = sorted(
            [*self.molecule_extraction, *self.document_layout],
            key=lambda item: (item.bbox[1], item.bbox[0], item.region_type),
        )
        for index, region in enumerate(regions, start=1):
            region.document_id = page.document_id
            region.page_number = page.page_number
            region.region_id = f"p{page.page_number:03d}_r{index:03d}"
        return regions


class SplitDocumentRegionDetector(BaseMoleculeRegionDetector):
    """Keep molecule extraction independent from non-molecule document layout."""

    name = "split-molecule-layout"

    def __init__(
        self,
        molecule_detector: BaseMoleculeRegionDetector,
        layout_detector: BaseMoleculeRegionDetector,
    ) -> None:
        self.molecule_detector = molecule_detector
        self.layout_detector = layout_detector

    def detect_streams(self, page: DocumentPage) -> DocumentRegionStreams:
        shared_regions: list[DocumentRegion] | None = None
        molecule_fallback = getattr(self.molecule_detector, "fallback", None)
        layout_fallback = getattr(self.layout_detector, "fallback", None)
        if (
            isinstance(molecule_fallback, HeuristicMoleculeRegionDetector)
            and isinstance(layout_fallback, HeuristicMoleculeRegionDetector)
            and molecule_fallback.proposal_config == layout_fallback.proposal_config
            and molecule_fallback.crop_screening_config == layout_fallback.crop_screening_config
            and not getattr(getattr(self.molecule_detector, "trainable", None), "available", False)
            and not getattr(getattr(self.layout_detector, "trainable", None), "available", False)
        ):
            shared_regions = self.molecule_detector.detect(page)
        molecule_source = shared_regions if shared_regions is not None else self.molecule_detector.detect(page)
        layout_source = shared_regions if shared_regions is not None else self.layout_detector.detect(page)
        molecule_regions = [
            region for region in molecule_source
            if region.region_type == "molecule"
        ]
        layout_regions = [
            region for region in layout_source
            if region.region_type != "molecule"
        ]
        for region in molecule_regions:
            region.source = "molecule_extraction"
        for region in layout_regions:
            region.source = "document_layout"
        return DocumentRegionStreams(molecule_regions, layout_regions)

    def detect(self, page: DocumentPage) -> list[DocumentRegion]:
        return self.detect_streams(page).combined(page)


def page_quality(image: np.ndarray) -> dict[str, Any]:
    """Compute simple page-quality diagnostics before region detection."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    foreground = gray < 245
    height, width = gray.shape[:2]
    contrast = float(np.percentile(gray, 95) - np.percentile(gray, 5))
    ink_ratio = float(np.mean(foreground))
    return {
        "width": int(width),
        "height": int(height),
        "pixel_count": int(width * height),
        "contrast": round(contrast, 3),
        "ink_ratio": round(ink_ratio, 5),
        "blank": bool(ink_ratio < 0.0005 or (contrast < 5.0 and ink_ratio < 0.002)),
        "too_large": bool(width * height > config.DOCUMENT_MAX_PIXELS),
    }


class HeuristicMoleculeRegionDetector(BaseMoleculeRegionDetector):
    """Detect molecule-like drawing regions without a large ML model."""

    name = "heuristic-opencv"

    def __init__(
        self,
        min_area: int = config.DOCUMENT_MIN_REGION_AREA,
        max_area_ratio: float = config.DOCUMENT_MAX_REGION_AREA_RATIO,
        max_regions: int = config.DOCUMENT_MAX_REGIONS,
        proposal_config: str | CandidateProposalConfig = "baseline",
        crop_screening_config: str | CropScreeningConfig = "candidate",
        screening_config: str | CandidateScreeningConfig | None = None,
    ) -> None:
        self.min_area = min_area
        self.max_area_ratio = max_area_ratio
        self.max_regions = max_regions
        if screening_config is not None:
            import warnings
            warnings.warn(
                "screening_config is deprecated; use proposal_config and crop_screening_config",
                DeprecationWarning,
                stacklevel=2,
            )
            legacy_name = screening_config.name if isinstance(screening_config, CandidateScreeningConfig) else screening_config
            proposal_config = legacy_name
            crop_screening_config = legacy_name
        self.proposal_config = get_proposal_config(proposal_config)
        self.crop_screening_config = get_crop_screening_config(crop_screening_config)

    def propose(self, page: DocumentPage) -> list[DocumentRegion]:
        """Form raw page-level boxes without applying crop classification."""
        image = cv2.imdecode(np.fromfile(str(Path(page.image_path)), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Unable to decode page image: {page.image_path}")
        return self._propose_from_image(page, image)

    def _propose_from_image(self, page: DocumentPage, image: np.ndarray) -> list[DocumentRegion]:
        quality = page_quality(image)
        page.quality = quality
        if quality["blank"] or quality["too_large"]:
            return []
        binary = self._foreground_binary(image)
        boxes = self._candidate_contours(binary, image.shape[1], image.shape[0])
        return [DocumentRegion(
            document_id=page.document_id,
            page_number=page.page_number,
            region_id=f"p{page.page_number:03d}_r{index:03d}",
            bbox=bbox,
            region_type="molecule",
            detector_name=self.name,
            message=f"proposal_config={self.proposal_config.name}",
        ) for index, bbox in enumerate(boxes[: self.max_regions], start=1)]

    def detect(self, page: DocumentPage) -> list[DocumentRegion]:
        image = cv2.imdecode(np.fromfile(str(Path(page.image_path)), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Unable to decode page image: {page.image_path}")
        proposed = self._propose_from_image(page, image)
        regions: list[DocumentRegion] = []
        for proposed_region in proposed:
            bbox = proposed_region.bbox
            screening = screen_region_candidate(
                image,
                bbox,
                "molecule",
                None,
                config=self.crop_screening_config,
                text_boxes=page.text_boxes,
                figure_boxes=page.figure_boxes,
            )
            recommended = screening.recommended_region_type
            region_type = "reaction_like" if recommended == "reaction" else (
                "unknown" if recommended == "uncertain" else recommended
            )
            confidence = screening.screening_score
            message = ", ".join(screening.reason_codes)
            if region_type == "unknown" and confidence < 0.28:
                continue
            region_id = f"p{page.page_number:03d}_r{len(regions) + 1:03d}"
            regions.append(DocumentRegion(
                document_id=page.document_id,
                page_number=page.page_number,
                region_id=region_id,
                bbox=bbox,
                region_type=region_type,
                detection_confidence=round(confidence, 3),
                detector_name=self.name,
                message=message,
                screening=screening.to_dict(),
            ))
            if len(regions) >= self.max_regions:
                break
        if not regions:
            binary = self._foreground_binary(image)
            fallback = self._whole_page_region(page, image, binary, image.shape[1], image.shape[0])
            if fallback is not None:
                regions.append(fallback)
        return regions

    @staticmethod
    def _foreground_binary(image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8), iterations=1)
        return binary

    def _candidate_contours(self, binary: np.ndarray, width: int, height: int) -> list[tuple[int, int, int, int]]:
        # A moderate dilation joins bonds, atom labels, and nearby ring strokes into one region.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, self.proposal_config.dilation_kernel)
        merged = cv2.dilate(binary, kernel, iterations=1)
        contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[int, int, int, int]] = []
        page_area = width * height
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < self.min_area:
                continue
            if area / max(page_area, 1) > self.max_area_ratio:
                continue
            if w < self.proposal_config.min_width or h < self.proposal_config.min_height:
                continue
            if w / max(h, 1) > self.proposal_config.max_horizontal_aspect and not (
                w >= self.proposal_config.wide_line_min_width
                and h >= self.proposal_config.wide_line_min_height
            ):
                continue
            if h / max(w, 1) > self.proposal_config.max_vertical_aspect:
                continue
            padding = self.proposal_config.bbox_padding
            x1 = max(0, x - padding)
            y1 = max(0, y - padding)
            x2 = min(width, x + w + padding)
            y2 = min(height, y + h + padding)
            candidates.append((x1, y1, x2, y2))
        return self._merge_overlapping_boxes(sorted(candidates, key=lambda item: (item[1], item[0])))

    def _merge_overlapping_boxes(self, boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
        """Merge highly overlapping boxes and discard near-duplicates."""
        merged: list[tuple[int, int, int, int]] = []
        for box in boxes:
            current = box
            changed = True
            while changed:
                changed = False
                remaining: list[tuple[int, int, int, int]] = []
                for existing in merged:
                    overlap = HeuristicMoleculeRegionDetector._overlap_ratio(current, existing)
                    if overlap >= self.proposal_config.merge_overlap_ratio:
                        current = (
                            min(current[0], existing[0]),
                            min(current[1], existing[1]),
                            max(current[2], existing[2]),
                            max(current[3], existing[3]),
                        )
                        changed = True
                    else:
                        remaining.append(existing)
                merged = remaining
            merged.append(current)
        return sorted(merged, key=lambda item: (item[1], item[0]))

    @staticmethod
    def _overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        inter_w = max(0, min(ax2, bx2) - max(ax1, bx1))
        inter_h = max(0, min(ay2, by2) - max(ay1, by1))
        intersection = inter_w * inter_h
        if intersection <= 0:
            return 0.0
        area_a = max((ax2 - ax1) * (ay2 - ay1), 1)
        area_b = max((bx2 - bx1) * (by2 - by1), 1)
        return intersection / min(area_a, area_b)

    def _classify(
        self,
        binary: np.ndarray,
        bbox: tuple[int, int, int, int],
        page_width: int,
        page_height: int,
    ) -> tuple[str, float, str]:
        x1, y1, x2, y2 = bbox
        crop = binary[y1:y2, x1:x2]
        width, height = x2 - x1, y2 - y1
        area = max(width * height, 1)
        ink_ratio = float(np.count_nonzero(crop) / area)
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats((crop > 0).astype(np.uint8), 8)
        component_areas = [int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, component_count)]
        significant_components = [value for value in component_areas if value >= 6]
        small_component_ratio = self._small_component_ratio(component_areas)
        text_line_count = self._text_line_count(crop)
        edges = cv2.Canny(crop, 60, 180)
        edge_ratio = float(np.count_nonzero(edges) / area)
        aspect = width / max(height, 1)
        horizontal_projection = np.max(np.sum(crop > 0, axis=1)) / max(width, 1)
        vertical_projection = np.max(np.sum(crop > 0, axis=0)) / max(height, 1)
        page_area_ratio = area / max(page_width * page_height, 1)
        _, long_line_count = self._line_segment_counts(crop)
        skeletal_linework = (
            long_line_count >= 8
            and len(significant_components) <= 55
            and ink_ratio < 0.12
            and small_component_ratio < 0.35
            and horizontal_projection < 0.55
            and vertical_projection < 0.55
        )

        if ink_ratio < 0.006:
            return "unknown", 0.05, "Sparse or blank region; not treated as a molecule."
        if width < 70 or height < 55:
            return "text", 0.45, "Region is too small for reliable single-molecule OCSR."
        if self._looks_like_table(crop, aspect, horizontal_projection, vertical_projection):
            return "table", 0.55, "Grid-like line structure; not sent to single-molecule OCSR by default."
        if self._looks_like_reaction_arrow(crop, aspect, width, height, ink_ratio):
            if len(significant_components) >= 6 or text_line_count >= 2:
                return "reaction_like", 0.64, "Reaction-like scheme; route to reaction review instead of single-molecule OCSR."
            return "reaction_arrow", 0.7, "Reaction arrow/line detected; route to reaction workflow."
        if self._looks_like_reaction_condition(
            width,
            height,
            aspect,
            ink_ratio,
            significant_components,
            text_line_count,
            small_component_ratio,
        ):
            return "reaction_condition", 0.57, "Short reaction-condition-like label; not a molecule crop."
        if self._looks_like_text(
            width,
            height,
            aspect,
            ink_ratio,
            significant_components,
            text_line_count,
            page_area_ratio,
            small_component_ratio,
            skeletal_linework,
        ):
            return "text", 0.68, "Text-like compact components; not treated as a molecule."
        if self._looks_like_figure(ink_ratio, page_area_ratio, edge_ratio):
            return "figure", 0.52, "Dense figure-like image region; requires manual review before OCSR."

        confidence = 0.25
        if 0.012 <= ink_ratio <= 0.24:
            confidence += 0.2
        if edge_ratio > 0.02:
            confidence += 0.15
        if len(significant_components) >= 3:
            confidence += 0.15
        if skeletal_linework:
            confidence += 0.16
        if 0.25 <= aspect <= 4.5:
            confidence += 0.12
        if 0.003 <= page_area_ratio <= 0.55:
            confidence += 0.08
        if aspect > 3.8 or aspect < 0.22:
            confidence -= 0.12
        if ink_ratio > 0.32:
            confidence -= 0.15
        confidence = min(confidence, 0.95)
        if confidence >= 0.68:
            return "molecule", confidence, "Detected by OpenCV line/foreground-density fallback."
        return "unknown", confidence, "Region did not meet molecule confidence threshold."

    def _whole_page_region(
        self,
        page: DocumentPage,
        image: np.ndarray,
        binary: np.ndarray,
        page_width: int,
        page_height: int,
    ) -> DocumentRegion | None:
        coordinates = cv2.findNonZero((binary > 0).astype(np.uint8))
        if coordinates is None:
            return None
        x, y, width, height = cv2.boundingRect(coordinates)
        padding = self.proposal_config.fallback_padding
        bbox = (
            max(0, x - padding),
            max(0, y - padding),
            min(page_width, x + width + padding),
            min(page_height, y + height + padding),
        )
        screening = screen_region_candidate(
            image,
            bbox,
            "molecule",
            None,
            config=self.crop_screening_config,
            text_boxes=page.text_boxes,
            figure_boxes=page.figure_boxes,
        )
        confidence = screening.screening_score
        message = ", ".join(screening.reason_codes)
        if not screening.molecule_candidate:
            return None
        return DocumentRegion(
            document_id=page.document_id,
            page_number=page.page_number,
            region_id=f"p{page.page_number:03d}_r001",
            bbox=bbox,
            region_type="molecule",
            detection_confidence=round(min(confidence, 0.82), 3),
            detector_name=self.name,
            message=message + " Whole-page fallback was used.",
            screening=screening.to_dict(),
        )

    @staticmethod
    def _looks_like_text(
        width: int,
        height: int,
        aspect: float,
        ink_ratio: float,
        significant_components: list[int],
        text_line_count: int = 0,
        page_area_ratio: float = 0.0,
        small_component_ratio: float = 0.0,
        skeletal_linework: bool = False,
    ) -> bool:
        if skeletal_linework:
            return False
        if text_line_count >= 5 and len(significant_components) >= 22 and ink_ratio < 0.30:
            return True
        if page_area_ratio > 0.035 and text_line_count >= 4 and len(significant_components) >= 18 and aspect > 0.75:
            return True
        if len(significant_components) >= 35 and small_component_ratio > 0.72 and ink_ratio < 0.26:
            return True
        if height <= 45 and aspect > 2.2 and len(significant_components) >= 3:
            return True
        if height <= 90 and aspect > 1.7 and len(significant_components) >= 2 and ink_ratio < 0.24:
            return True
        if aspect > 4.2 and ink_ratio < 0.24 and len(significant_components) >= 4:
            return True
        if width < 140 and height < 60 and len(significant_components) >= 2:
            return True
        if len(significant_components) >= 12 and aspect > 1.3 and height < 240 and ink_ratio < 0.22:
            return True
        return False

    @staticmethod
    def _text_line_count(crop: np.ndarray) -> int:
        """Estimate text rows from horizontal ink runs."""
        if crop.size == 0:
            return 0
        height, width = crop.shape[:2]
        row_ink = np.sum(crop > 0, axis=1) / max(width, 1)
        active = row_ink > 0.012
        line_count = 0
        run_length = 0
        for value in active:
            if value:
                run_length += 1
            else:
                if run_length >= max(2, int(height * 0.006)):
                    line_count += 1
                run_length = 0
        if run_length >= max(2, int(height * 0.006)):
            line_count += 1
        return line_count

    @staticmethod
    def _small_component_ratio(component_areas: list[int]) -> float:
        if not component_areas:
            return 0.0
        small = sum(1 for area in component_areas if 6 <= area <= 80)
        return small / max(len(component_areas), 1)

    @staticmethod
    def _line_segment_counts(crop: np.ndarray) -> tuple[int, int]:
        """Estimate structural linework without treating text strokes as molecule evidence."""
        if crop.size == 0:
            return 0, 0
        height, width = crop.shape[:2]
        lines = cv2.HoughLinesP(
            crop,
            1,
            np.pi / 180,
            threshold=25,
            minLineLength=max(22, min(width, height) // 12),
            maxLineGap=5,
        )
        if lines is None:
            return 0, 0
        total = 0
        long_segments = 0
        for line in lines.reshape(-1, 4):
            x1, y1, x2, y2 = [int(value) for value in line]
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            total += 1
            if length >= max(32, min(width, height) * 0.10):
                long_segments += 1
        return total, long_segments

    @staticmethod
    def _looks_like_table(crop: np.ndarray, aspect: float, horizontal_projection: float, vertical_projection: float) -> bool:
        if aspect < 0.5 or aspect > 6:
            return False
        horizontal_lines = horizontal_projection > 0.65
        vertical_lines = vertical_projection > 0.65
        return bool(horizontal_lines and vertical_lines)

    @staticmethod
    def _looks_like_reaction_arrow(
        crop: np.ndarray,
        aspect: float,
        width: int,
        height: int,
        ink_ratio: float,
    ) -> bool:
        if aspect < 2.5 or width < 180 or height > 190 or ink_ratio > 0.18:
            return False
        lines = cv2.HoughLinesP(crop, 1, np.pi / 180, threshold=40, minLineLength=max(60, width // 4), maxLineGap=8)
        if lines is None:
            return False
        long_horizontal = 0
        for line in lines.reshape(-1, 4):
            x1, y1, x2, y2 = [int(value) for value in line]
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if length > width * 0.25 and abs(y2 - y1) <= max(4, height * 0.08):
                long_horizontal += 1
        return long_horizontal >= 1

    @staticmethod
    def _looks_like_reaction(
        crop: np.ndarray,
        aspect: float,
        width: int,
        height: int,
        ink_ratio: float | None = None,
    ) -> bool:
        if ink_ratio is None:
            area = max(width * height, 1)
            ink_ratio = float(np.count_nonzero(crop) / area)
        return HeuristicMoleculeRegionDetector._looks_like_reaction_arrow(crop, aspect, width, height, ink_ratio)

    @staticmethod
    def _looks_like_reaction_condition(
        width: int,
        height: int,
        aspect: float,
        ink_ratio: float,
        significant_components: list[int],
        text_line_count: int,
        small_component_ratio: float,
    ) -> bool:
        if height < 55 or height > 145:
            return False
        if aspect < 1.7 or aspect > 8.0:
            return False
        if not (0.008 <= ink_ratio <= 0.20):
            return False
        if not (1 <= text_line_count <= 3):
            return False
        if not (3 <= len(significant_components) <= 28):
            return False
        return small_component_ratio >= 0.25

    @staticmethod
    def _looks_like_figure(ink_ratio: float, page_area_ratio: float, edge_ratio: float) -> bool:
        return bool(page_area_ratio > 0.025 and ink_ratio > 0.34 and edge_ratio > 0.025)
