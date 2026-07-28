"""Tests for auditable collection without live PubChem/PMC or model requests."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from PIL import Image

from src.datasets.http import CachedHttpClient
from src.datasets.licenses import is_allowed_license, normalize_license
from src.datasets.pipeline import DatasetPipeline
from src.datasets.pmc import PmcOpenAccessCollector
from src.datasets.provenance import SourceRecord, SourceRegistry
from src.datasets.review import DatasetReviewStore
from src.datasets.splits import assign_grouped_splits, validate_split_isolation


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": "application/octet-stream"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float, headers: dict[str, str] | None = None) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(self.responses[url])


class FakeOCSRResult:
    def __init__(self, backend: str) -> None:
        self.backend = backend

    def to_dict(self) -> dict[str, str]:
        return {"backend": self.backend, "status": "success", "smiles": "CCO", "message": "fake"}


class FakeRecognizer:
    def __init__(self, backend: str) -> None:
        self.backend = backend

    def recognize(self, image: Path) -> FakeOCSRResult:
        assert image.is_file()
        return FakeOCSRResult(self.backend)


class TrackingRecognizer(FakeRecognizer):
    created: list[str] = []

    def __init__(self, backend: str) -> None:
        super().__init__(backend)
        self.created.append(backend)


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 80), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _source() -> SourceRecord:
    return SourceRecord(
        source_key="pubchem:702",
        source_kind="pubchem",
        source_id="702",
        source_url="https://example.test/702",
        license="Public Domain (PubChem)",
        license_allowed=True,
        retrieved_at="2026-01-01T00:00:00+00:00",
        attribution="PubChem CID 702",
    )


def test_license_normalizes_explicit_creative_commons_urls() -> None:
    assert normalize_license("https://creativecommons.org/licenses/by/4.0/") == "cc-by-4.0"
    assert normalize_license("http://creativecommons.org/licenses/by-sa/3.0/") == "cc-by-sa-3.0"
    assert normalize_license("https://creativecommons.org/publicdomain/zero/1.0/") == "cc0-1.0"
    assert is_allowed_license("Creative Commons: https://creativecommons.org/licenses/by/4.0/")
    assert not is_allowed_license("Creative Commons Attribution license")


def test_pubchem_collection_records_properties_sdf_png_and_uses_cache(tmp_path: Path, monkeypatch) -> None:
    properties_url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/702/property/CanonicalSMILES,IsomericSMILES,InChIKey/JSON"
    sdf_url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/702/record/SDF?record_type=2d"
    png_url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/702/PNG?image_size=1000x1000"
    session = FakeSession({
        properties_url: json.dumps({"PropertyTable": {"Properties": [{"CID": 702, "SMILES": "CCO", "InChIKey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"}]}}).encode(),
        sdf_url: b"ethanol\n  RDKit\n",
        png_url: _png_bytes(),
    })
    pipeline = DatasetPipeline(
        tmp_path,
        client=CachedHttpClient(tmp_path / "cache", session=session, request_interval=0),
        recognizer_factory=FakeRecognizer,
    )
    monkeypatch.setattr(pipeline, "_queue_for_existing_review", lambda row, predictions: "queue.json")

    result = pipeline.collect_pubchem(702)

    assert result["status"] == "completed"
    assert len(session.calls) == 3
    source = SourceRegistry(tmp_path).get("pubchem:702")
    assert source is not None
    assert source["license_allowed"] == "true"
    row = pipeline.pending_manifest.read_text(encoding="utf-8")
    assert "molscribe" in row and "decimer" in row and "ensemble" in row


def test_negative_candidate_records_not_applicable_predictions_without_model_call(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "text.png"
    image.write_bytes(_png_bytes())
    pipeline = DatasetPipeline(tmp_path, recognizer_factory=FakeRecognizer)
    monkeypatch.setattr(
        pipeline,
        "_predict_all",
        lambda _: (_ for _ in ()).throw(AssertionError("negative candidate must not invoke OCSR")),
    )

    pipeline.add_candidate(image, _source(), category="text", source_document="doc-1")

    row = next(csv.DictReader(pipeline.pending_manifest.open(encoding="utf-8")))
    predictions = json.loads(row["candidate_predictions"])
    assert [item["status"] for item in predictions] == ["not_applicable"] * 3


def test_review_needed_is_queued_without_running_ocsr(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "mixed.png"
    image.write_bytes(_png_bytes())
    pipeline = DatasetPipeline(tmp_path, recognizer_factory=FakeRecognizer)
    monkeypatch.setattr(pipeline, "_queue_for_existing_review", lambda row, predictions: "visual-review.json")
    monkeypatch.setattr(
        pipeline, "_predict_all",
        lambda _: (_ for _ in ()).throw(AssertionError("review_needed must not invoke OCSR")),
    )
    pipeline.add_candidate(
        image, _source(), category="uncertain_visual", source_document="doc-1",
        screening_decision="review_needed", screening_config_version="crop-screening-candidate-v1",
    )
    row = next(csv.DictReader(pipeline.pending_manifest.open(encoding="utf-8")))
    assert row["expected_action"] == "review"
    assert row["queue_annotation_path"] == "visual-review.json"
    assert json.loads(row["candidate_predictions"])[0]["status"] == "not_applicable"


def test_positive_candidate_builds_ensemble_from_existing_backend_results(tmp_path: Path) -> None:
    image = tmp_path / "molecule.png"
    image.write_bytes(_png_bytes())
    TrackingRecognizer.created = []
    pipeline = DatasetPipeline(tmp_path, recognizer_factory=TrackingRecognizer)

    predictions = pipeline._predict_all(image)

    assert TrackingRecognizer.created == ["molscribe", "decimer"]
    assert [item["backend"] for item in predictions] == ["molscribe", "decimer", "ensemble"]
    assert predictions[-1]["candidates"] is not None


def test_pmc_unknown_license_registers_metadata_without_pdf_download(tmp_path: Path) -> None:
    oa_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC1234567"
    xml_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1234567/fullTextXML"
    pdf_url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/pdf/"
    session = FakeSession({
        oa_url: b"<OA><records><record id='PMC1234567'><link href='https://example.test/package.tar.gz'/></record></records></OA>",
        xml_url: b"<article><front><article-meta><title-group><article-title>Example</article-title></title-group></article-meta></front></article>",
    })
    collector = PmcOpenAccessCollector(CachedHttpClient(tmp_path / "cache", session=session, request_interval=0), tmp_path / "sources")

    result = collector.collect("PMC1234567", document_url=pdf_url)

    assert result.document_path is None
    assert result.source.license_allowed is False
    assert session.calls == [oa_url, xml_url]


def test_pmc_not_listed_as_open_access_never_downloads_pdf(tmp_path: Path) -> None:
    oa_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC7654321"
    xml_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC7654321/fullTextXML"
    session = FakeSession({
        oa_url: b"<OA><records /></OA>",
        xml_url: b"<article><front><article-meta><permissions><license><license-p>CC BY 4.0</license-p></license></permissions></article-meta></front></article>",
    })
    collector = PmcOpenAccessCollector(CachedHttpClient(tmp_path / "cache", session=session, request_interval=0), tmp_path / "sources")

    result = collector.collect("PMC7654321")

    assert result.document_path is None
    assert result.source.license_allowed is False
    assert session.calls == [oa_url, xml_url]


def test_pmc_allowed_page_image_is_materialized_only_after_oa_and_license_checks(tmp_path: Path) -> None:
    oa_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC1111111"
    xml_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1111111/fullTextXML"
    page_url = "https://example.test/PMC1111111-page-1.png"
    session = FakeSession({
        oa_url: b"<OA><records><record id='PMC1111111'><link href='https://example.test/package.tar.gz'/></record></records></OA>",
        xml_url: b"<article><front><article-meta><permissions><license><license-p>CC BY 4.0</license-p></license></permissions></article-meta></front></article>",
        page_url: _png_bytes(),
    })
    collector = PmcOpenAccessCollector(CachedHttpClient(tmp_path / "cache", session=session, request_interval=0), tmp_path / "sources")

    result = collector.collect("PMC1111111", document_url=page_url)

    assert result.source.license_allowed is True
    assert result.document_path is not None and result.document_path.suffix == ".png"
    assert session.calls == [oa_url, xml_url, page_url]


def test_pmc_allowed_default_download_uses_official_aws_article_pdf(tmp_path: Path) -> None:
    pmcid = "PMC2222222"
    oa_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}"
    xml_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    listing_url = f"https://pmc-oa-opendata.s3.amazonaws.com/?list-type=2&prefix={pmcid}.&delimiter=/"
    pdf_url = f"https://pmc-oa-opendata.s3.amazonaws.com/{pmcid}.2/{pmcid}.2.pdf"
    session = FakeSession({
        oa_url: f"<OA><records><record id='{pmcid}'><link href='https://example.test/package.tar.gz'/></record></records></OA>".encode(),
        xml_url: b"<article><front><article-meta><permissions><license><license-p>CC BY 4.0</license-p></license></permissions></article-meta></front></article>",
        listing_url: f"<ListBucketResult><CommonPrefixes><Prefix>{pmcid}.1/</Prefix></CommonPrefixes><CommonPrefixes><Prefix>{pmcid}.2/</Prefix></CommonPrefixes></ListBucketResult>".encode(),
        pdf_url: b"%PDF-1.7 fake article",
    })
    collector = PmcOpenAccessCollector(CachedHttpClient(tmp_path / "cache", session=session, request_interval=0), tmp_path / "sources")

    result = collector.collect(pmcid)

    assert result.document_path is not None and result.document_path.suffix == ".pdf"
    assert result.source.source_url == pdf_url
    assert session.calls == [oa_url, xml_url, listing_url, pdf_url]


def test_two_person_review_is_required_before_verified_manifest(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(_png_bytes())
    pipeline = DatasetPipeline(tmp_path, recognizer_factory=FakeRecognizer)
    monkeypatch.setattr(pipeline, "_queue_for_existing_review", lambda row, predictions: "feedback/annotation.json")
    candidate = pipeline.add_candidate(image, _source(), category="molecule", source_document="pubchem:702")
    reviews = DatasetReviewStore(tmp_path)

    reviews.record_vote(candidate.sample_id, "alice", "approve", smiles="CCO")
    first = reviews.build_verified_manifest()
    reviews.record_vote(candidate.sample_id, "bob", "approve", smiles="CCO")
    second = reviews.build_verified_manifest()

    assert first["verified_count"] == 0
    assert second["verified_count"] == 1
    assert Path(second["output_manifest"]).is_file()


def test_grouped_split_prevents_source_identity_and_scaffold_leakage() -> None:
    rows = [
        {"sample_id": "a", "source_document": "doc-1", "ground_truth_inchikey": "A", "ground_truth_smiles": "c1ccccc1"},
        {"sample_id": "b", "source_document": "doc-1", "ground_truth_inchikey": "B", "ground_truth_smiles": "CCO"},
        {"sample_id": "c", "source_document": "doc-2", "ground_truth_inchikey": "A", "ground_truth_smiles": "c1ccccc1"},
        {"sample_id": "d", "source_document": "doc-3", "ground_truth_inchikey": "D", "ground_truth_smiles": "CCN"},
    ]

    assigned = assign_grouped_splits(rows)

    assert not validate_split_isolation(assigned)
    splits = {row["sample_id"]: row["split"] for row in assigned}
    assert splits["a"] == splits["b"] == splits["c"]
