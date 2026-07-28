"""Page-level evaluation for raw OpenCV region proposals."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from src.documents.candidate_screening import get_proposal_config
from src.documents.detectors import HeuristicMoleculeRegionDetector
from src.documents.models import DocumentPage


def bbox_iou(first: list[int] | tuple[int, ...], second: list[int] | tuple[int, ...]) -> float:
    ax1, ay1, ax2, ay2 = first; bx1, by1, bx2, by2 = second
    width = max(0, min(ax2, bx2) - max(ax1, bx1))
    height = max(0, min(ay2, by2) - max(ay1, by1))
    intersection = width * height
    union = max((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection, 1)
    return intersection / union


def _intersection_fraction(first: list[int] | tuple[int, ...], second: list[int] | tuple[int, ...]) -> float:
    ax1, ay1, ax2, ay2 = first; bx1, by1, bx2, by2 = second
    intersection = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(0, min(ay2, by2) - max(ay1, by1))
    return intersection / max((bx2 - bx1) * (by2 - by1), 1)


def match_boxes(
    truth: list[list[int]], proposals: list[list[int]], *, iou_threshold: float = 0.5,
    event_overlap_threshold: float = 0.1,
) -> dict[str, Any]:
    pairs = sorted(
        ((bbox_iou(gt, proposal), gt_index, proposal_index)
         for gt_index, gt in enumerate(truth) for proposal_index, proposal in enumerate(proposals)),
        reverse=True,
    )
    used_truth: set[int] = set(); used_proposals: set[int] = set(); matches = []
    for iou, gt_index, proposal_index in pairs:
        if iou < iou_threshold:
            break
        if gt_index in used_truth or proposal_index in used_proposals:
            continue
        used_truth.add(gt_index); used_proposals.add(proposal_index)
        matches.append({"truth_index": gt_index, "proposal_index": proposal_index, "iou": iou})
    merged = sum(
        sum(_intersection_fraction(proposal, gt) >= event_overlap_threshold for gt in truth) >= 2
        for proposal in proposals
    )
    split = sum(
        sum(_intersection_fraction(gt, proposal) >= event_overlap_threshold for proposal in proposals) >= 2
        for gt in truth
    )
    return {
        "matches": matches,
        "missed_truth_indices": sorted(set(range(len(truth))) - used_truth),
        "false_proposal_indices": sorted(set(range(len(proposals))) - used_proposals),
        "merged_region_errors": merged, "split_truth_errors": split,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_page_proposals(
    dataset: Path, output: Path, *, proposal_config: str, iou_threshold: float = 0.5,
) -> dict[str, Any]:
    dataset = dataset.resolve(); output = output.resolve(); output.mkdir(parents=True, exist_ok=True)
    annotations = json.loads((dataset / "annotations.json").read_text(encoding="utf-8"))
    protocol = json.loads((dataset / "protocol.json").read_text(encoding="utf-8"))
    settings = get_proposal_config(proposal_config)
    config_json = json.dumps(asdict(settings), sort_keys=True, separators=(",", ":"))
    config_sha = hashlib.sha256(config_json.encode()).hexdigest()
    checksums_file = dataset / "checksums.sha256"
    dataset_checksums_sha = hashlib.sha256(checksums_file.read_bytes()).hexdigest() if checksums_file.is_file() else ""
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    detector = HeuristicMoleculeRegionDetector(proposal_config=settings, crop_screening_config="candidate")
    page_rows: list[dict[str, Any]] = []; matches_rows: list[dict[str, Any]] = []
    missed_rows: list[dict[str, Any]] = []; false_rows: list[dict[str, Any]] = []
    gallery = output / "comparison_gallery"; gallery.mkdir(exist_ok=True)
    aggregate = Counter(); document_totals: dict[str, Counter] = defaultdict(Counter)
    class_counts = Counter()
    for page_id, page in sorted(annotations.get("pages", {}).items()):
        if page.get("annotation_status") != "completed":
            raise ValueError(f"Page is not completed: {page_id}")
        image_path = dataset / page["image_path"]
        model_page = DocumentPage(
            document_id=page["source_document"], page_number=int(page["page_number"]),
            image_path=str(image_path), width=int(page["width"]), height=int(page["height"]),
        )
        proposals = [list(region.bbox) for region in detector.propose(model_page)]
        truth_items = page.get("annotations", [])
        for item in truth_items: class_counts[item["class"]] += 1
        truth = [item["bbox"] for item in truth_items if item["class"] == "molecule"]
        result = match_boxes(truth, proposals, iou_threshold=iou_threshold)
        tp = len(result["matches"]); fn = len(result["missed_truth_indices"]); fp = len(result["false_proposal_indices"])
        mean_iou = sum(item["iou"] for item in result["matches"]) / tp if tp else 0.0
        values = Counter(
            truth=tp + fn, proposals=tp + fp, tp=tp, fn=fn, fp=fp,
            merged=result["merged_region_errors"], split=result["split_truth_errors"],
        )
        aggregate.update(values); document_totals[page["source_document"]].update(values)
        document_totals[page["source_document"]]["iou_sum_micros"] += round(mean_iou * tp * 1_000_000)
        aggregate["iou_sum_micros"] += round(mean_iou * tp * 1_000_000)
        row = {
            "page_id": page_id, "source_document": page["source_document"], "page_number": page["page_number"],
            "molecule_truth": tp + fn, "proposal_count": tp + fp, "true_positive": tp,
            "missed_molecules": fn, "false_proposals": fp,
            "merged_region_errors": result["merged_region_errors"], "split_truth_errors": result["split_truth_errors"],
            "precision": round(_ratio(tp, tp + fp), 6), "recall": round(_ratio(tp, tp + fn), 6),
            "mean_iou": round(mean_iou, 6),
        }
        page_rows.append(row)
        for match in result["matches"]:
            matches_rows.append({"page_id": page_id, **match, "truth_bbox": json.dumps(truth[match["truth_index"]]), "proposal_bbox": json.dumps(proposals[match["proposal_index"]])})
        for index in result["missed_truth_indices"]:
            missed_rows.append({"page_id": page_id, "source_document": page["source_document"], "page_number": page["page_number"], "truth_index": index, "bbox": json.dumps(truth[index])})
        for index in result["false_proposal_indices"]:
            false_rows.append({"page_id": page_id, "source_document": page["source_document"], "page_number": page["page_number"], "proposal_index": index, "bbox": json.dumps(proposals[index])})
        preview = Image.open(image_path).convert("RGB"); draw = ImageDraw.Draw(preview)
        for box in truth: draw.rectangle(tuple(box), outline="#00a86b", width=4)
        for box in proposals: draw.rectangle(tuple(box), outline="#e74c3c", width=3)
        preview.save(gallery / f"{page_id}.jpg", quality=88)
    tp, fp, fn = aggregate["tp"], aggregate["fp"], aggregate["fn"]
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    metrics = {
        "dataset_role": "page_holdout", "proposal_config": settings.name, "git_sha": git_sha,
        "proposal_config_sha256": config_sha, "dataset_config_sha256": protocol.get("config_sha256"),
        "dataset_checksums_sha256": dataset_checksums_sha,
        "iou_threshold": iou_threshold, "page_count": len(page_rows), "molecule_truth_count": aggregate["truth"],
        "proposal_count": aggregate["proposals"], "true_positive": tp, "missed_molecule_count": fn,
        "false_proposal_count": fp, "molecule_proposal_precision": round(precision, 6),
        "molecule_proposal_recall": round(recall, 6), "molecule_proposal_f1": round(f1, 6),
        "mean_matched_iou": round(aggregate["iou_sum_micros"] / max(tp, 1) / 1_000_000, 6),
        "merged_region_error_count": aggregate["merged"], "split_truth_error_count": aggregate["split"],
        "ground_truth_class_counts": dict(sorted(class_counts.items())),
        "proposal_class_counts": {"molecule_candidate": aggregate["proposals"]},
    }
    document_rows = []
    for document, values in sorted(document_totals.items()):
        doc_precision = _ratio(values["tp"], values["tp"] + values["fp"])
        doc_recall = _ratio(values["tp"], values["tp"] + values["fn"])
        document_rows.append({
            "source_document": document, "molecule_truth": values["truth"], "proposal_count": values["proposals"],
            "true_positive": values["tp"], "missed_molecules": values["fn"], "false_proposals": values["fp"],
            "merged_region_errors": values["merged"], "split_truth_errors": values["split"],
            "precision": round(doc_precision, 6), "recall": round(doc_recall, 6),
            "f1": round(2 * doc_precision * doc_recall / (doc_precision + doc_recall), 6) if doc_precision + doc_recall else 0.0,
            "mean_iou": round(values["iou_sum_micros"] / max(values["tp"], 1) / 1_000_000, 6),
        })
    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output / "per_page_metrics.csv", page_rows, list(page_rows[0]) if page_rows else [])
    _write_csv(output / "per_document_metrics.csv", document_rows, list(document_rows[0]) if document_rows else [])
    _write_csv(output / "matches.csv", matches_rows, ["page_id", "truth_index", "proposal_index", "iou", "truth_bbox", "proposal_bbox"])
    _write_csv(output / "missed_molecules.csv", missed_rows, ["page_id", "source_document", "page_number", "truth_index", "bbox"])
    _write_csv(output / "false_proposals.csv", false_rows, ["page_id", "source_document", "page_number", "proposal_index", "bbox"])
    (output / "report.md").write_text(
        f"# Page proposal evaluation: {settings.name}\n\n"
        f"- Git SHA: `{git_sha}`\n- Proposal config SHA-256: `{config_sha}`\n"
        f"- Dataset checksums SHA-256: `{dataset_checksums_sha or 'unfrozen-test-fixture'}`\n"
        f"- IoU threshold: {iou_threshold}\n- Precision: {precision:.4f}\n- Recall: {recall:.4f}\n- F1: {f1:.4f}\n"
        f"- Missed molecules: {fn}\n- False proposals: {fp}\n- Merged errors: {aggregate['merged']}\n- Split errors: {aggregate['split']}\n\n"
        "This page-level dataset measures bbox proposal formation on the 30 frozen annotated pages. "
        "It does not estimate recall outside those pages or end-to-end OCSR structure accuracy.\n",
        encoding="utf-8",
    )
    return metrics
