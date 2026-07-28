from __future__ import annotations

import json
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import cv2
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

import config
from src.documents.detectors import (
    HeuristicMoleculeRegionDetector,
    HybridMoleculeRegionDetector,
    SplitDocumentRegionDetector,
    TrainableMoleculeRegionDetector,
)
from src.documents.input_loader import DocumentInputError, DocumentInputLoader, PDFRenderer, check_file_size
from src.documents.models import DocumentPage, DocumentRegion
from src.documents.processor import DocumentOCSRProcessor


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_page(path: Path, molecule_names: list[str] | None = None, text: str = "") -> Path:
    page = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(page)
    if text:
        draw.text((60, 30), text, fill="black")
    molecule_names = molecule_names or []
    for index, name in enumerate(molecule_names):
        source = PROJECT_ROOT / "data" / "samples" / f"{name}.png"
        image = Image.open(source).convert("RGB").resize((260, 220))
        page.paste(image, (80 + index * 420, 130))
    path.parent.mkdir(parents=True, exist_ok=True)
    page.save(path)
    return path


def _make_pdf(path: Path, pages: int = 1) -> Path:
    pdf = canvas.Canvas(str(path))
    for index in range(pages):
        pdf.drawString(72, 720, f"Generated fixture page {index + 1}")
        pdf.showPage()
    pdf.save()
    return path


class FakePDFRenderer(PDFRenderer):
    def __init__(self, fixture_pages: list[Path]) -> None:
        super().__init__()
        self.fixture_pages = fixture_pages

    def render(self, pdf_path: str | Path, output_dir: str | Path, document_id: str) -> list[DocumentPage]:
        pages: list[DocumentPage] = []
        page_dir = Path(output_dir) / "pages"
        page_dir.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(self.fixture_pages, start=1):
            destination = page_dir / f"{document_id}_p{index:03d}.png"
            destination.write_bytes(source.read_bytes())
            with Image.open(destination) as image:
                width, height = image.size
            pages.append(DocumentPage(
                document_id=document_id,
                page_number=index,
                image_path=str(destination),
                width=width,
                height=height,
                source_path=str(pdf_path),
                source_type="pdf",
                render_dpi=200,
            ))
        return pages


class FakeDetector:
    name = "fake-detector"

    def __init__(self, regions: list[DocumentRegion]) -> None:
        self.regions = regions

    def detect(self, page: DocumentPage) -> list[DocumentRegion]:
        return [
            DocumentRegion(
                document_id=page.document_id,
                page_number=page.page_number,
                region_id=region.region_id,
                bbox=region.bbox,
                region_type=region.region_type,
                detection_confidence=region.detection_confidence,
                detector_name=self.name,
                confirmed=region.confirmed,
            )
            for region in self.regions
        ]


class FakeReportGenerator:
    def __init__(self) -> None:
        self.calls: list[Path] = []
        self.recognizer = types.SimpleNamespace(backend="demo")

    def generate(self, image_path: str | Path) -> dict:
        self.calls.append(Path(image_path))
        return {
            "status": "success",
            "message": "fake ok",
            "input": {},
            "ocsr": {"backend": "demo", "status": "success", "smiles": "CCO"},
            "final": {"smiles": "CCO", "canonical_smiles": "CCO"},
        }


class FailingReportGenerator(FakeReportGenerator):
    def generate(self, image_path: str | Path) -> dict:
        self.calls.append(Path(image_path))
        return {
            "status": "failed",
            "message": "fake failure",
            "input": {"path": str(image_path)},
            "ocsr": {"backend": "demo", "status": "failed"},
            "final": {},
        }


class SingleAtomReportGenerator(FakeReportGenerator):
    def generate(self, image_path: str | Path) -> dict:
        self.calls.append(Path(image_path))
        return {
            "status": "success",
            "message": "model returned candidate",
            "input": {},
            "ocsr": {"backend": "fake", "status": "success", "smiles": "C"},
            "final": {"smiles": "C", "canonical_smiles": "C"},
        }


def test_single_page_pdf_processes_with_fake_renderer(tmp_path: Path) -> None:
    page = _make_page(tmp_path / "aspirin_page.png", ["aspirin"], text="single molecule")
    pdf = _make_pdf(tmp_path / "aspirin_doc.pdf")
    loader = DocumentInputLoader(tmp_path / "out", renderer=FakePDFRenderer([page]))
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", loader=loader, crop_screening_config="baseline")
    result = processor.process(pdf)
    assert result["summary"]["page_count"] == 1
    assert result["summary"]["molecule_region_count"] >= 1
    assert result["summary"]["recognized_region_count"] == 0
    first = next(region for region in result["regions"] if region["region_type"] == "molecule")
    assert first["page_number"] == 1
    assert len(first["bbox"]) == 4
    assert first["confirmed"] is False
    assert first["crop_path"] is None
    assert Path(result["exports"]["json"]).is_file()
    assert Path(result["exports"]["regions_csv"]).is_file()
    assert Path(result["exports"]["structures_sdf"]).is_file()
    assert Path(result["exports"]["structures_smi"]).is_file()
    assert Path(result["exports"]["structures_zip"]).is_file()
    assert Path(result["exports"]["structure_failed_csv"]).is_file()
    assert Path(result["exports"]["structure_review_csv"]).is_file()
    assert Path(result["exports"]["zip"]).is_file()


def test_full_document_workflow_reports_render_and_page_detection_progress(tmp_path: Path) -> None:
    first = _make_page(tmp_path / "page_1.png", [], text="page one")
    second = _make_page(tmp_path / "page_2.png", [], text="page two")
    pdf = _make_pdf(tmp_path / "paper.pdf", pages=2)
    loader = DocumentInputLoader(tmp_path / "out", renderer=FakePDFRenderer([first, second]))
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", loader=loader, detector=FakeDetector([]))
    events: list[tuple[str, int, int, str]] = []

    result = processor.process(
        pdf,
        run_ocsr=False,
        document_progress_callback=lambda stage, current, total, detail: events.append((stage, current, total, detail)),
    )

    assert events[0][:3] == ("rendered", 2, 2)
    assert [event[:3] for event in events[1:]] == [("detecting", 1, 2), ("detecting", 2, 2)]
    assert result["processing"]["workflow"] == "full_document_review"
    assert result["processing"]["processed_page_count"] == 2


def test_real_pymupdf_pdf_rendering_when_available(tmp_path: Path) -> None:
    pytest.importorskip("fitz")
    pdf_path = tmp_path / "aspirin_real_pdf.pdf"
    pdf = canvas.Canvas(str(pdf_path))
    pdf.drawString(72, 720, "Aspirin PDF fixture")
    pdf.drawImage(
        str(PROJECT_ROOT / "data" / "samples" / "aspirin.png"),
        90,
        420,
        width=260,
        height=195,
        preserveAspectRatio=True,
        mask="auto",
    )
    pdf.showPage()
    pdf.save()

    result = DocumentOCSRProcessor("demo", tmp_path / "out").process(pdf_path)

    assert result["summary"]["page_count"] == 1
    assert result["summary"]["molecule_region_count"] >= 1
    assert result["summary"]["recognized_region_count"] == 0
    molecule_region = next(region for region in result["regions"] if region["region_type"] == "molecule")
    assert molecule_region["page_number"] == 1
    assert molecule_region["bbox"][2] > molecule_region["bbox"][0]
    assert molecule_region["confirmed"] is False
    assert any(box["text"] == "Aspirin" for box in result["pages"][0]["text_boxes"])
    assert result["pages"][0]["figure_boxes"]
    assert Path(result["exports"]["json"]).is_file()


def test_multi_page_pdf_and_blank_page(tmp_path: Path) -> None:
    molecule_page = _make_page(tmp_path / "aspirin_page.png", ["aspirin"])
    blank_page = _make_page(tmp_path / "blank.png", [])
    pdf = _make_pdf(tmp_path / "aspirin_multi.pdf", pages=2)
    loader = DocumentInputLoader(tmp_path / "out", renderer=FakePDFRenderer([molecule_page, blank_page]))
    result = DocumentOCSRProcessor("demo", tmp_path / "out", loader=loader, crop_screening_config="baseline").process(pdf, run_ocsr=False)
    assert result["summary"]["page_count"] == 2
    assert any(page["quality"].get("blank") for page in result["pages"])
    assert result["summary"]["molecule_region_count"] >= 1


def test_detect_only_mode_does_not_call_ocsr(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "aspirin_page.png", ["aspirin"])
    detector = FakeDetector([
        DocumentRegion("doc", 1, "p001_r001", (70, 120, 360, 380), "molecule", 0.9),
    ])
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", detector=detector, crop_screening_config="baseline")
    fake_generator = FakeReportGenerator()
    processor.report_generator = fake_generator

    result = processor.process(page_path, run_ocsr=False)

    assert result["processing"]["mode"] == "detect_only"
    assert fake_generator.calls == []
    assert result["regions"][0]["status"] == "detected"


def test_full_document_mode_does_not_recognize_unconfirmed_regions(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "aspirin_page.png", ["aspirin"])
    detector = FakeDetector([
        DocumentRegion("doc", 1, "p001_r001", (70, 120, 360, 380), "molecule", 0.9),
    ])
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", detector=detector, crop_screening_config="baseline")
    fake_generator = FakeReportGenerator()
    processor.report_generator = fake_generator

    result = processor.process(page_path, run_ocsr=True)

    assert fake_generator.calls == []
    assert result["processing"]["candidate_region_count"] == 1
    assert result["processing"]["confirmed_candidate_region_count"] == 0
    assert result["regions"][0]["status"] == "detected"
    assert "等待人工确认" in result["regions"][0]["message"]


def test_default_processor_uses_split_molecule_and_layout_detector(tmp_path: Path) -> None:
    processor = DocumentOCSRProcessor("demo", tmp_path / "out")
    assert isinstance(processor.detector, SplitDocumentRegionDetector)
    assert isinstance(processor.detector.molecule_detector, HybridMoleculeRegionDetector)
    assert isinstance(processor.detector.molecule_detector.fallback, HeuristicMoleculeRegionDetector)
    assert processor.detector.molecule_detector.fallback.proposal_config.name == "baseline"
    assert processor.detector.layout_detector.fallback.proposal_config.name == "baseline"


def test_split_streams_do_not_overwrite_layout_or_molecule_regions(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "stream_fixture.png", [], text="fixture")
    page = DocumentPage("doc", 1, str(page_path), 900, 600)
    molecule_detector = FakeDetector([
        DocumentRegion("doc", 1, "m1", (100, 100, 300, 300), "molecule", 0.9),
        DocumentRegion("doc", 1, "ignored-text", (10, 10, 200, 60), "text", 0.8),
    ])
    layout_detector = FakeDetector([
        DocumentRegion("doc", 1, "l1", (10, 10, 200, 60), "text", 0.8),
        DocumentRegion("doc", 1, "l2", (50, 350, 350, 430), "reaction_like", 0.8),
        DocumentRegion("doc", 1, "l3", (400, 100, 800, 400), "table", 0.8),
        DocumentRegion("doc", 1, "ignored-molecule", (100, 100, 300, 300), "molecule", 0.9),
    ])
    detector = SplitDocumentRegionDetector(molecule_detector, layout_detector)
    streams = detector.detect_streams(page)
    assert [region.region_type for region in streams.molecule_extraction] == ["molecule"]
    assert {region.region_type for region in streams.document_layout} == {"text", "reaction_like", "table"}
    assert all(region.source == "molecule_extraction" for region in streams.molecule_extraction)
    assert all(region.source == "document_layout" for region in streams.document_layout)
    assert len(streams.combined(page)) == 4


def test_candidate_molecule_stream_preserves_baseline_layout_on_mixed_page(tmp_path: Path) -> None:
    page_image = Image.new("RGB", (1000, 850), "white")
    draw = ImageDraw.Draw(page_image)
    for index in range(8):
        draw.text((40, 20 + index * 28), "Ordinary document body text remains in layout output.", fill="black")
    molecule = Image.open(PROJECT_ROOT / "data" / "samples" / "caffeine.png").convert("RGB").resize((420, 300))
    page_image.paste(molecule, (460, 430))
    page_path = tmp_path / "candidate_molecule_baseline_layout.png"
    page_image.save(page_path)
    processor = DocumentOCSRProcessor(
        "demo", tmp_path / "out", proposal_config="candidate",
        document_layout_proposal_config="baseline", crop_screening_config="candidate",
    )
    streams = processor.detect_page_streams(DocumentPage("doc", 1, str(page_path), 1000, 850))
    assert any(region.region_type == "molecule" for region in streams.molecule_extraction)
    assert any(region.region_type == "text" for region in streams.document_layout)


def test_baseline_layout_stream_preserves_table_fixture(tmp_path: Path) -> None:
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    for x in range(100, 801, 140):
        draw.line((x, 100, x, 500), fill="black", width=4)
    for y in range(100, 501, 80):
        draw.line((100, y, 800, y), fill="black", width=4)
    page_path = tmp_path / "table_fixture.png"
    image.save(page_path)
    processor = DocumentOCSRProcessor(
        "demo", tmp_path / "out", proposal_config="candidate",
        document_layout_proposal_config="baseline", crop_screening_config="candidate",
    )
    streams = processor.detect_page_streams(DocumentPage("doc", 1, str(page_path), 900, 600))
    assert any(region.region_type == "table" for region in streams.document_layout)
    assert not streams.molecule_extraction


def test_full_document_mode_only_recognizes_screened_molecule_regions(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "aspirin_page.png", ["aspirin"], text="Aspirin")
    detector = FakeDetector([
        DocumentRegion("doc", 1, "p001_r001", (70, 120, 360, 380), "molecule", 0.9, confirmed=True),
        DocumentRegion("doc", 1, "p001_r002", (45, 20, 340, 70), "molecule", 0.8, confirmed=True),
        DocumentRegion("doc", 1, "p001_r003", (30, 480, 400, 540), "text", 0.8),
    ])
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", detector=detector, crop_screening_config="baseline")
    fake_generator = FakeReportGenerator()
    processor.report_generator = fake_generator

    result = processor.process(page_path, run_ocsr=True)

    assert result["processing"]["mode"] == "detect_and_recognize"
    assert result["processing"]["confirmed_candidate_region_count"] == 1
    assert len(fake_generator.calls) == 1
    statuses = {region["region_id"]: region for region in result["regions"]}
    assert statuses["p001_r001"]["status"] == "recognized"
    assert statuses["p001_r002"]["status"] == "skipped"
    assert any(value in statuses["p001_r002"]["message"] for value in ("文字", "文本", "过小"))
    assert statuses["p001_r003"]["status"] == "skipped"


def test_model_output_complexity_mismatch_is_rejected_after_inference(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "complex_input.png", ["caffeine"])
    page = DocumentPage("doc", 1, str(page_path), 900, 600)
    region = DocumentRegion(
        "doc",
        1,
        "p001_r001",
        (70, 120, 360, 380),
        "molecule",
        0.9,
        confirmed=True,
        screening={
            "diagnostics": {
                "structural_evidence": True,
                "long_line_count": 18,
                "valid_component_count": 5,
                "ring_count": 2,
                "branch_junction_count": 1,
            }
        },
    )
    processor = DocumentOCSRProcessor("demo", tmp_path / "out")
    processor.report_generator = SingleAtomReportGenerator()

    processor.recognize_region(region, [page], tmp_path / "out", screen=False)

    gate = region.report["recognition_gate"]["input_output_complexity"]
    assert region.status == "failed"
    assert gate["passed"] is False
    assert gate["reason_code"] == "output_too_simple_for_input"
    assert region.final_result == {}
    assert region.report["rejected_candidate"]["canonical_smiles"] == "C"


def test_existing_document_can_be_rescreened_without_ocsr(tmp_path: Path) -> None:
    image = Image.new("RGB", (300, 180), "white")
    image_path = tmp_path / "old_label_region.png"
    image.save(image_path)
    array = cv2.imread(str(image_path))
    cv2.putText(array, "(B)", (85, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.imwrite(str(image_path), array)
    result = {
        "document_id": "old-doc",
        "output_dir": str(tmp_path),
        "pages": [{"page_number": 1, "width": 300, "height": 180, "image_path": str(image_path)}],
        "regions": [{
            "document_id": "old-doc",
            "page_number": 1,
            "region_id": "p001_r001",
            "bbox": [65, 60, 175, 125],
            "region_type": "molecule",
            "detection_confidence": 0.99,
            "source": "detector",
            "status": "detected",
            "confirmed": False,
            "screening": {"config_version": "crop-screening-candidate-v2"},
        }],
        "summary": {},
        "exports": {},
        "detection_errors": [],
    }
    processor = DocumentOCSRProcessor("demo", tmp_path / "out")
    processor.export = lambda updated, _output: updated.get("exports", {})  # type: ignore[method-assign]

    refreshed = processor.rescreen_document_result(result)
    region = refreshed["regions"][0]

    assert result["regions"][0]["region_type"] == "molecule"
    assert region["region_type"] == "text"
    assert region["screening"]["config_version"] == "crop-screening-candidate-v3"
    assert region["audit"][-1]["operation"] == "automatic_rescreen"
    assert refreshed["processing"]["screening_refresh"]["changed_region_count"] == 1


def test_invalid_bbox_is_not_sent_to_ocsr(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "aspirin_page.png", ["aspirin"])
    page = DocumentPage("doc", 1, str(page_path), 900, 600)
    region = DocumentRegion("doc", 1, "p001_r001", (100, 100, 100, 120), "molecule", 0.9, confirmed=True)
    processor = DocumentOCSRProcessor("demo", tmp_path / "out")
    fake_generator = FakeReportGenerator()
    processor.report_generator = fake_generator

    processor.recognize_region(region, [page], tmp_path / "out")

    assert region.status == "skipped"
    assert fake_generator.calls == []
    assert region.screening["passed"] is False


def test_damaged_pdf_reports_readable_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    broken_pdf = tmp_path / "broken.pdf"
    broken_pdf.write_bytes(b"%PDF broken")

    fake_fitz = types.SimpleNamespace(open=lambda _path: (_ for _ in ()).throw(RuntimeError("cannot open xref")))
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    with pytest.raises(DocumentInputError, match="damaged|encrypted|unsupported"):
        PDFRenderer().render(broken_pdf, tmp_path / "out", "broken")


def test_multi_molecule_page_and_text_not_molecule(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "aspirin_benzene_page.png", ["aspirin", "benzene"], text="Two molecule page")
    page = DocumentPage("doc", 1, str(page_path), 900, 600)
    regions = HeuristicMoleculeRegionDetector(crop_screening_config="baseline").detect(page)
    molecule_regions = [region for region in regions if region.region_type == "molecule"]
    text_regions = [region for region in regions if region.region_type == "text"]
    assert len(molecule_regions) >= 2
    assert text_regions


def test_trainable_detector_predictions_can_be_hybridized(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "trainable_page.png", [], text="")
    page = DocumentPage("doc", 1, str(page_path), 900, 600)

    trainable = TrainableMoleculeRegionDetector(
        predictor=lambda _page: [
            {
                "bbox": [100, 120, 320, 300],
                "region_type": "molecule",
                "confidence": 0.91,
                "message": "model candidate",
            }
        ],
        name="layout-model",
    )
    detector = HybridMoleculeRegionDetector(trainable=trainable, fallback=HeuristicMoleculeRegionDetector())

    regions = detector.detect(page)

    assert regions[0].region_type == "molecule"
    assert regions[0].detection_confidence == 0.91
    assert regions[0].detector_name == "layout-model"


def test_complex_linework_is_not_filtered_as_text() -> None:
    assert HeuristicMoleculeRegionDetector._looks_like_text(
        width=480,
        height=300,
        aspect=1.6,
        ink_ratio=0.055,
        significant_components=[20] * 22,
        text_line_count=5,
        page_area_ratio=0.08,
        small_component_ratio=0.06,
        skeletal_linework=True,
    ) is False
    assert HeuristicMoleculeRegionDetector._looks_like_text(
        width=480,
        height=300,
        aspect=1.6,
        ink_ratio=0.055,
        significant_components=[20] * 22,
        text_line_count=5,
        page_area_ratio=0.08,
        small_component_ratio=0.06,
        skeletal_linework=False,
    ) is True


def test_complex_molecule_page_keeps_text_and_structure_separate(tmp_path: Path) -> None:
    page_image = Image.new("RGB", (1000, 850), "white")
    draw = ImageDraw.Draw(page_image)
    lines = [
        "Rivaroxaban prevents thromboembolism during surgical operations.",
        "The methods are simple and accurate and do not require equipment.",
    ]
    for index, line in enumerate(lines * 6):
        draw.text((40, 20 + index * 28), line, fill="black")
    caffeine = Image.open(PROJECT_ROOT / "data" / "samples" / "caffeine.png").convert("RGB").resize((420, 300))
    page_image.paste(caffeine, (460, 430))
    page_path = tmp_path / "complex_molecule_with_text.png"
    page_image.save(page_path)

    regions = HeuristicMoleculeRegionDetector().detect(DocumentPage("doc", 1, str(page_path), 1000, 850))

    assert any(region.region_type == "text" for region in regions)
    assert any(region.region_type == "molecule" and region.bbox[0] > 400 for region in regions)


def test_no_molecule_page_does_not_emit_molecule_regions(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "text_only.png", [], text="This page contains text but no structure")
    page = DocumentPage("doc", 1, str(page_path), 900, 600)
    regions = HeuristicMoleculeRegionDetector().detect(page)
    assert not [region for region in regions if region.region_type == "molecule"]


def test_multiline_text_block_is_filtered_before_ocsr(tmp_path: Path) -> None:
    page_image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(page_image)
    lines = [
        "This paragraph describes aspirin and rivaroxaban in an article.",
        "It contains many compact character components but no molecule.",
        "The detector should avoid sending this text block to OCSR.",
        "A single bad region must not waste DECIMER inference time.",
        "Only real chemical structure crops should be recognized.",
        "Figure captions and labels are not molecular structures.",
    ]
    for index, line in enumerate(lines):
        draw.text((80, 80 + index * 32), line, fill="black")
    page_path = tmp_path / "text_block.png"
    page_image.save(page_path)

    page = DocumentPage("doc", 1, str(page_path), 900, 600)
    region = DocumentRegion("doc", 1, "p001_r001", (50, 50, 820, 310), "molecule", 0.9, confirmed=True)
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", crop_screening_config="baseline")
    fake_generator = FakeReportGenerator()
    processor.report_generator = fake_generator

    processor.recognize_region(region, [page], tmp_path / "out")

    assert region.status == "skipped"
    assert fake_generator.calls == []
    assert region.screening["passed"] is False
    assert region.screening["text_line_count"] >= 4


def test_reaction_like_region_is_not_ocrd_as_single_molecule(tmp_path: Path) -> None:
    page = Image.new("RGB", (700, 260), "white")
    draw = ImageDraw.Draw(page)
    draw.line((80, 130, 620, 130), fill="black", width=4)
    draw.polygon([(620, 130), (590, 115), (590, 145)], fill="black")
    page_path = tmp_path / "reaction_like.png"
    page.save(page_path)
    result = DocumentOCSRProcessor("demo", tmp_path / "out").process(page_path)
    reaction_regions = [region for region in result["regions"] if region["region_type"] in {"reaction_arrow", "reaction_like"}]
    assert reaction_regions
    assert all(region["status"] != "recognized" for region in reaction_regions)
    assert all("单分子识别" in str(region["message"]) for region in reaction_regions)


def test_reaction_condition_region_is_not_sent_to_single_molecule_ocsr(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "condition_page.png", [])
    page = DocumentPage("doc", 1, str(page_path), 900, 600)
    region = DocumentRegion("doc", 1, "p001_r001", (100, 100, 500, 180), "reaction_condition", 0.8)
    processor = DocumentOCSRProcessor("demo", tmp_path / "out")
    fake_generator = FakeReportGenerator()
    processor.report_generator = fake_generator

    processor.recognize_region(region, [page], tmp_path / "out")

    assert region.status == "skipped"
    assert "反应" in str(region.message)
    assert fake_generator.calls == []


def test_zip_image_collection_is_supported(tmp_path: Path) -> None:
    image_a = _make_page(tmp_path / "aspirin_page.png", ["aspirin"])
    image_b = _make_page(tmp_path / "benzene_page.png", ["benzene"])
    archive = tmp_path / "pages.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(image_a, "a/aspirin_page.png")
        zf.write(image_b, "b/benzene_page.png")
    result = DocumentOCSRProcessor("demo", tmp_path / "out").process(archive, run_ocsr=False)
    assert result["summary"]["page_count"] == 2


def test_region_editing_records_audit_and_reruns(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "aspirin_page.png", ["aspirin"])
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", crop_screening_config="baseline")
    result = processor.process(page_path, run_ocsr=False)
    page = result["pages"][0]
    edited = processor.apply_edits(
        result,
        [{
            "action": "add",
            "page_number": page["page_number"],
            "bbox": [70, 120, 360, 380],
            "region_type": "molecule",
            "confirmed": True,
        }],
        rerun_ocsr=True,
    )
    added = next(region for region in edited["regions"] if region["source"] == "user")
    assert added["audit"]
    assert added["status"] == "recognized"
    assert added["report"]["input"]["bbox"] == added["bbox"]


def test_region_merge_split_page_confirm_and_annotation_export(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "two_region_page.png", ["aspirin", "benzene"])
    detector = FakeDetector([
        DocumentRegion("doc", 1, "p001_r001", (70, 120, 330, 370), "molecule", 0.9),
        DocumentRegion("doc", 1, "p001_r002", (490, 120, 790, 380), "text", 0.7),
    ])
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", detector=detector)
    result = processor.process(page_path, run_ocsr=False)

    confirmed = processor.apply_edits(
        result,
        [{"action": "confirm_page", "page_number": 1}],
        rerun_ocsr=False,
    )
    assert confirmed["summary"]["confirmed_region_count"] == 2

    merged = processor.apply_edits(
        confirmed,
        [{
            "action": "merge",
            "region_ids": ["p001_r001", "p001_r002"],
            "region_type": "molecule",
            "confirmed": True,
        }],
        rerun_ocsr=False,
    )
    active = [region for region in merged["regions"] if region["status"] != "deleted"]
    assert len(active) == 1
    assert active[0]["bbox"] == [70, 120, 790, 380]
    assert active[0]["confirmed"] is True
    assert any(event["operation"] == "merge" for event in active[0]["audit"])

    split = processor.apply_edits(
        merged,
        [{
            "action": "split",
            "region_id": active[0]["region_id"],
            "direction": "vertical",
            "split_at": 0.5,
            "region_type": "molecule",
            "confirmed": True,
        }],
        rerun_ocsr=False,
    )
    active_split = [region for region in split["regions"] if region["status"] != "deleted"]
    assert len(active_split) == 2
    assert all(region["confirmed"] for region in active_split)
    assert split["summary"]["confirmed_region_count"] == 2

    annotations_path = Path(split["exports"]["detection_annotations_json"])
    payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    assert payload["summary"]["region_count"] == 2
    assert [region["label"] for region in payload["annotations"][0]["regions"]] == ["molecule", "molecule"]


def test_document_detection_annotation_export_script(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "export_page.png", ["aspirin"])
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", detector=FakeDetector([
        DocumentRegion("doc", 1, "p001_r001", (70, 120, 360, 380), "molecule", 0.9),
    ]))
    result = processor.process(page_path, run_ocsr=False)
    result = processor.apply_edits(result, [{"action": "confirm_page", "page_number": 1}], rerun_ocsr=False)
    output = tmp_path / "annotations.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "export_document_detection_annotations.py"),
            "--document-result",
            result["exports"]["json"],
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["region_count"] == 1
    assert payload["annotations"][0]["regions"][0]["bbox"] == [70, 120, 360, 380]


def test_single_region_failure_does_not_stop_other_regions_and_enters_review_queue(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "unknown_page.png", ["aspirin", "benzene"])
    detector = FakeDetector([
        DocumentRegion("doc", 1, "p001_r001", (70, 120, 360, 380), "molecule", 0.9, confirmed=True),
        DocumentRegion("doc", 1, "p001_r002", (490, 120, 780, 380), "molecule", 0.9, confirmed=True),
    ])
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", detector=detector, review_output_dir=tmp_path / "data", crop_screening_config="baseline")
    failing_generator = FailingReportGenerator()
    processor.report_generator = failing_generator

    result = processor.process(page_path)

    molecule_regions = [region for region in result["regions"] if region["region_type"] == "molecule"]
    assert len(molecule_regions) == 2
    assert all(region["status"] == "failed" for region in molecule_regions)
    assert all((region.get("review") or {}).get("queued") for region in molecule_regions)
    assert len(failing_generator.calls) == 2
    assert result["summary"]["review_queue_count"] == 2
    assert Path(result["exports"]["failure_cases_csv"]).is_file()
    assert Path(tmp_path / "data" / "feedback" / "manifest.csv").is_file()


def test_file_size_limit_is_reported(tmp_path: Path) -> None:
    big_file = tmp_path / "large.png"
    big_file.write_bytes(b"0" * 100)
    with pytest.raises(DocumentInputError, match="safety limit"):
        check_file_size(big_file, max_size_mb=0.00001)


def test_region_csv_contains_coordinates_and_final_result(tmp_path: Path) -> None:
    page_path = _make_page(tmp_path / "aspirin_page.png", ["aspirin"])
    detector = FakeDetector([
        DocumentRegion("doc", 1, "p001_r001", (70, 120, 360, 380), "molecule", 0.9, confirmed=True),
    ])
    processor = DocumentOCSRProcessor("demo", tmp_path / "out", detector=detector, crop_screening_config="baseline")
    processor.report_generator = FakeReportGenerator()
    result = processor.process(page_path)
    frame = pd.read_csv(result["exports"]["regions_csv"])
    assert {
        "document_id",
        "page_number",
        "region_id",
        "bbox_x1",
        "bbox_y1",
        "bbox_x2",
        "bbox_y2",
        "confirmed",
    }.issubset(frame.columns)
    molecule_rows = frame[frame["region_type"] == "molecule"]
    assert not molecule_rows.empty
    assert "final_smiles" in frame.columns
