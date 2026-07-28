"""Page-level evaluation of proposal formation plus crop-screening OCSR routing."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.documents.candidate_screening import (
    get_crop_screening_config,
    get_proposal_config,
    screen_region_candidate,
)
from src.documents.detectors import HeuristicMoleculeRegionDetector
from src.documents.models import DocumentPage
from src.evaluation.page_proposals import bbox_iou, match_boxes


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def evaluate_routed_boxes(
    truth: list[list[int]],
    routed: list[dict[str, Any]],
    *,
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    """Evaluate routed proposal dictionaries for one page."""

    accepted_items = [item for item in routed if item["decision"] == "accept_molecule"]
    accepted_boxes = [item["bbox"] for item in accepted_items]
    matched = match_boxes(truth, accepted_boxes, iou_threshold=iou_threshold)
    duplicates = sum(
        max(0, sum(bbox_iou(gt, proposal) >= iou_threshold for proposal in accepted_boxes) - 1)
        for gt in truth
    )
    return {
        "accepted_items": accepted_items,
        "accepted_boxes": accepted_boxes,
        "matches": matched["matches"],
        "missed_truth_indices": matched["missed_truth_indices"],
        "false_accepted_indices": matched["false_proposal_indices"],
        "duplicate_accepted_boxes": duplicates,
        "merged_region_errors": matched["merged_region_errors"],
        "split_truth_errors": matched["split_truth_errors"],
        "review_needed_items": [item for item in routed if item["decision"] == "review_needed"],
        "rejected_items": [item for item in routed if item["decision"] == "reject_negative"],
    }


def evaluate_page_ocsr_routing(
    dataset: Path,
    output: Path,
    *,
    proposal_config: str,
    crop_screening_config: str = "candidate",
    iou_threshold: float = 0.5,
) -> dict[str, Any]:
    """Run proposal formation and crop screening without invoking an OCSR model."""

    dataset = dataset.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    annotations = json.loads((dataset / "annotations.json").read_text(encoding="utf-8"))
    protocol = json.loads((dataset / "protocol.json").read_text(encoding="utf-8"))
    proposal_settings = get_proposal_config(proposal_config)
    crop_settings = get_crop_screening_config(crop_screening_config)
    proposal_json = json.dumps(asdict(proposal_settings), sort_keys=True, separators=(",", ":"))
    crop_json = json.dumps(asdict(crop_settings), sort_keys=True, separators=(",", ":"))
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    checksums_file = dataset / "checksums.sha256"
    checksums_sha = hashlib.sha256(checksums_file.read_bytes()).hexdigest() if checksums_file.is_file() else ""
    detector = HeuristicMoleculeRegionDetector(
        proposal_config=proposal_settings,
        crop_screening_config=crop_settings,
    )

    page_rows: list[dict[str, Any]] = []
    accepted_rows: list[dict[str, Any]] = []
    false_rows: list[dict[str, Any]] = []
    missed_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    aggregate = Counter()
    document_totals: dict[str, Counter] = defaultdict(Counter)

    for page_id, page in sorted(annotations.get("pages", {}).items()):
        if page.get("annotation_status") != "completed":
            raise ValueError(f"Page is not completed: {page_id}")
        image_path = dataset / page["image_path"]
        image = cv2.imdecode(np.fromfile(str(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Unable to decode page image: {image_path}")
        model_page = DocumentPage(
            document_id=page["source_document"],
            page_number=int(page["page_number"]),
            image_path=str(image_path),
            width=int(page["width"]),
            height=int(page["height"]),
        )
        proposed = detector.propose(model_page)
        routed: list[dict[str, Any]] = []
        for proposal_index, region in enumerate(proposed):
            screening = screen_region_candidate(
                image,
                region.bbox,
                region.region_type,
                region.detection_confidence,
                config=crop_settings,
            )
            routed.append({
                "proposal_index": proposal_index,
                "bbox": list(region.bbox),
                "decision": screening.decision,
                "recommended_region_type": screening.recommended_region_type,
                "screening_score": screening.screening_score,
                "reason_codes": list(screening.reason_codes),
            })
        truth = [
            item["bbox"] for item in page.get("annotations", [])
            if item.get("class") == "molecule"
        ]
        result = evaluate_routed_boxes(truth, routed, iou_threshold=iou_threshold)
        tp = len(result["matches"])
        fn = len(result["missed_truth_indices"])
        fp = len(result["false_accepted_indices"])
        accepted_count = len(result["accepted_items"])
        review_count = len(result["review_needed_items"])
        rejected_count = len(result["rejected_items"])
        values = Counter(
            pages=1,
            truth=len(truth),
            proposals=len(routed),
            accepted=accepted_count,
            tp=tp,
            fn=fn,
            fp=fp,
            review=review_count,
            rejected=rejected_count,
            duplicates=result["duplicate_accepted_boxes"],
            merged=result["merged_region_errors"],
            split=result["split_truth_errors"],
        )
        aggregate.update(values)
        document_totals[page["source_document"]].update(values)
        precision = _ratio(tp, accepted_count)
        recall = _ratio(tp, len(truth))
        page_rows.append({
            "page_id": page_id,
            "source_document": page["source_document"],
            "page_number": page["page_number"],
            "molecule_truth": len(truth),
            "proposal_count": len(routed),
            "accepted_boxes": accepted_count,
            "matched_molecules": tp,
            "missed_molecules": fn,
            "false_accepted_boxes": fp,
            "accepted_box_precision": round(precision, 6),
            "molecule_routing_recall": round(recall, 6),
            "ocsr_calls": accepted_count,
            "review_needed": review_count,
            "rejected_proposals": rejected_count,
            "duplicate_accepted_boxes": result["duplicate_accepted_boxes"],
            "merged_region_errors": result["merged_region_errors"],
            "split_truth_errors": result["split_truth_errors"],
        })
        for match in result["matches"]:
            accepted = result["accepted_items"][match["proposal_index"]]
            accepted_rows.append({
                "page_id": page_id,
                "source_document": page["source_document"],
                "truth_index": match["truth_index"],
                "proposal_index": accepted["proposal_index"],
                "iou": match["iou"],
                "truth_bbox": json.dumps(truth[match["truth_index"]]),
                "accepted_bbox": json.dumps(accepted["bbox"]),
                "screening_score": accepted["screening_score"],
                "reason_codes": "|".join(accepted["reason_codes"]),
            })
        for accepted_index in result["false_accepted_indices"]:
            accepted = result["accepted_items"][accepted_index]
            false_rows.append({
                "page_id": page_id,
                "source_document": page["source_document"],
                "page_number": page["page_number"],
                "proposal_index": accepted["proposal_index"],
                "bbox": json.dumps(accepted["bbox"]),
                "screening_score": accepted["screening_score"],
                "reason_codes": "|".join(accepted["reason_codes"]),
            })
        for truth_index in result["missed_truth_indices"]:
            missed_rows.append({
                "page_id": page_id,
                "source_document": page["source_document"],
                "page_number": page["page_number"],
                "truth_index": truth_index,
                "bbox": json.dumps(truth[truth_index]),
            })
        for item in result["review_needed_items"]:
            review_rows.append({
                "page_id": page_id,
                "source_document": page["source_document"],
                "page_number": page["page_number"],
                "proposal_index": item["proposal_index"],
                "bbox": json.dumps(item["bbox"]),
                "recommended_region_type": item["recommended_region_type"],
                "screening_score": item["screening_score"],
                "reason_codes": "|".join(item["reason_codes"]),
            })

    precision = _ratio(aggregate["tp"], aggregate["accepted"])
    recall = _ratio(aggregate["tp"], aggregate["truth"])
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    metrics = {
        "dataset_role": "page_holdout",
        "evaluation_scope": "proposal formation plus crop screening; OCSR model not invoked",
        "proposal_config": proposal_settings.name,
        "crop_screening_config": crop_settings.name,
        "git_sha": git_sha,
        "proposal_config_sha256": hashlib.sha256(proposal_json.encode()).hexdigest(),
        "crop_screening_config_sha256": hashlib.sha256(crop_json.encode()).hexdigest(),
        "dataset_config_sha256": protocol.get("config_sha256"),
        "dataset_checksums_sha256": checksums_sha,
        "iou_threshold": iou_threshold,
        "page_count": aggregate["pages"],
        "molecule_truth_count": aggregate["truth"],
        "proposal_count": aggregate["proposals"],
        "accepted_box_count": aggregate["accepted"],
        "matched_molecule_count": aggregate["tp"],
        "missed_molecule_count": aggregate["fn"],
        "false_accepted_box_count": aggregate["fp"],
        "molecule_routing_recall": round(recall, 6),
        "accepted_box_precision": round(precision, 6),
        "molecule_routing_f1": round(f1, 6),
        "ocsr_call_count": aggregate["accepted"],
        "ocsr_calls_per_page": round(_ratio(aggregate["accepted"], aggregate["pages"]), 6),
        "review_needed_count": aggregate["review"],
        "review_needed_per_page": round(_ratio(aggregate["review"], aggregate["pages"]), 6),
        "rejected_proposal_count": aggregate["rejected"],
        "rejected_proposals_per_page": round(_ratio(aggregate["rejected"], aggregate["pages"]), 6),
        "duplicate_accepted_box_count": aggregate["duplicates"],
        "merged_region_error_count": aggregate["merged"],
        "split_truth_error_count": aggregate["split"],
    }
    document_rows = []
    for document, values in sorted(document_totals.items()):
        doc_precision = _ratio(values["tp"], values["accepted"])
        doc_recall = _ratio(values["tp"], values["truth"])
        document_rows.append({
            "source_document": document,
            "page_count": values["pages"],
            "molecule_truth": values["truth"],
            "proposal_count": values["proposals"],
            "accepted_boxes": values["accepted"],
            "matched_molecules": values["tp"],
            "missed_molecules": values["fn"],
            "false_accepted_boxes": values["fp"],
            "accepted_box_precision": round(doc_precision, 6),
            "molecule_routing_recall": round(doc_recall, 6),
            "molecule_routing_f1": round(
                2 * doc_precision * doc_recall / (doc_precision + doc_recall), 6,
            ) if doc_precision + doc_recall else 0.0,
            "ocsr_calls_per_page": round(_ratio(values["accepted"], values["pages"]), 6),
            "review_needed": values["review"],
            "review_needed_per_page": round(_ratio(values["review"], values["pages"]), 6),
            "rejected_proposals": values["rejected"],
            "duplicate_accepted_boxes": values["duplicates"],
            "merged_region_errors": values["merged"],
            "split_truth_errors": values["split"],
        })

    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output / "per_page_metrics.csv", page_rows, list(page_rows[0]) if page_rows else [])
    _write_csv(output / "per_document_metrics.csv", document_rows, list(document_rows[0]) if document_rows else [])
    _write_csv(output / "accepted_matches.csv", accepted_rows, [
        "page_id", "source_document", "truth_index", "proposal_index", "iou",
        "truth_bbox", "accepted_bbox", "screening_score", "reason_codes",
    ])
    _write_csv(output / "false_accepted.csv", false_rows, [
        "page_id", "source_document", "page_number", "proposal_index", "bbox",
        "screening_score", "reason_codes",
    ])
    _write_csv(output / "missed_molecules.csv", missed_rows, [
        "page_id", "source_document", "page_number", "truth_index", "bbox",
    ])
    _write_csv(output / "review_needed.csv", review_rows, [
        "page_id", "source_document", "page_number", "proposal_index", "bbox",
        "recommended_region_type", "screening_score", "reason_codes",
    ])
    (output / "routing_report.md").write_text(
        f"# Page OCSR routing: {proposal_settings.name} + {crop_settings.name}\n\n"
        f"- Molecule routing recall: {recall:.4f}\n"
        f"- Accepted-box precision: {precision:.4f}\n"
        f"- Missed molecules: {aggregate['fn']}\n"
        f"- False accepted boxes: {aggregate['fp']}\n"
        f"- OCSR calls/page: {metrics['ocsr_calls_per_page']:.4f}\n"
        f"- Review needed/page: {metrics['review_needed_per_page']:.4f}\n"
        f"- Rejected proposals/page: {metrics['rejected_proposals_per_page']:.4f}\n"
        f"- Duplicate accepted boxes: {aggregate['duplicates']}\n\n"
        "Only accept_molecule boxes are counted as OCSR calls; no MolScribe or DECIMER model was run. "
        "The frozen page truth contains molecule boxes only, so this report does not validate document-layout class recall.\n",
        encoding="utf-8",
    )
    return metrics
