"""PMC Open Access collector with a strict license gate before downloads."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from src.datasets.http import CachedHttpClient
from src.datasets.licenses import is_allowed_license, normalize_license
from src.datasets.provenance import SourceRecord, sha256_bytes, utc_now_iso
from src.utils.file_utils import ensure_directory


EUROPE_PMC_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
PMC_PDF = "https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/"
PMC_OA_API = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}"
PMC_AWS_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"


@dataclass(frozen=True)
class PmcSource:
    pmcid: str
    source: SourceRecord
    document_path: Path | None = None


class PmcOpenAccessCollector:
    """Register PMC provenance first and download a PDF only after license approval."""

    def __init__(self, client: CachedHttpClient, material_root: str | Path) -> None:
        self.client = client
        self.material_root = ensure_directory(Path(material_root).expanduser().resolve())

    def collect(
        self,
        pmcid: str,
        *,
        document_url: str | None = None,
        dry_run: bool = False,
    ) -> PmcSource:
        pmcid = self._normalize_pmcid(pmcid)
        oa_url = PMC_OA_API.format(pmcid=pmcid)
        oa_payload, oa_metadata = self.client.get_bytes(oa_url)
        oa_record = self._parse_oa_record(oa_payload)
        metadata_url = EUROPE_PMC_XML.format(pmcid=pmcid)
        xml_payload, xml_metadata = self.client.get_bytes(metadata_url)
        metadata = self._parse_metadata(xml_payload)
        is_open_access = oa_record["is_open_access"]
        license_allowed = is_open_access and is_allowed_license(metadata["license"])
        source = SourceRecord(
            source_key=f"pmc:{pmcid}",
            source_kind="pmc_oa",
            source_id=pmcid,
            source_url=document_url or PMC_PDF.format(pmcid=pmcid),
            license=metadata["license"],
            license_allowed=license_allowed,
            retrieved_at=utc_now_iso(),
            source_sha256=xml_metadata["sha256"],
            attribution=metadata["attribution"],
            metadata={
                "oa_url": oa_url,
                "oa_sha256": oa_metadata["sha256"],
                "oa_package_url": oa_record["package_url"],
                "is_pmc_open_access": is_open_access,
                "metadata_url": metadata_url,
                "metadata_sha256": xml_metadata["sha256"],
                **metadata,
            },
        )
        # Crucial policy boundary: unknown/unapproved licenses remain registry-only.
        if dry_run or not source.license_allowed:
            return PmcSource(pmcid=pmcid, source=source)

        if document_url:
            resource_url = document_url
            resource_payload, resource_metadata = self.client.get_bytes(resource_url)
        else:
            resource_url, resource_payload, resource_metadata = self._download_official_pdf(pmcid)
        suffix, resource_type = self._material_type(resource_payload)
        if not suffix:
            raise ValueError(f"PMC source {pmcid} did not return a PDF or supported page image from {resource_url}.")
        document_path = ensure_directory(self.material_root / "pmc" / pmcid) / f"{pmcid}{suffix}"
        document_path.write_bytes(resource_payload)
        source = SourceRecord(
            **{
                **source.__dict__,
                "source_url": resource_url,
                "source_sha256": resource_metadata["sha256"],
                "metadata": {
                    **(source.metadata or {}),
                    "material_sha256": sha256_bytes(resource_payload),
                    "material_type": resource_type,
                    "material_url": resource_url,
                },
            }
        )
        return PmcSource(pmcid=pmcid, source=source, document_path=document_path)

    def _download_official_pdf(self, pmcid: str) -> tuple[str, bytes, dict[str, Any]]:
        """Download an article PDF from PMC's public anonymous AWS dataset.

        The interactive PMC ``/pdf/`` endpoint may reject automated requests even
        for Open Access articles. The PMC dataset service publishes the same
        permitted article objects in a public S3 bucket, indexed by article
        version. We enumerate only this article's version prefixes and retain the
        final object URL in provenance.
        """
        listing_url = f"{PMC_AWS_BUCKET}/?list-type=2&prefix={pmcid}.&delimiter=/"
        listing_payload, _ = self.client.get_bytes(listing_url)
        versions = self._parse_aws_versions(listing_payload, pmcid)
        if not versions:
            raise ValueError(f"PMC cloud dataset did not expose an article version for {pmcid}.")
        version = max(versions)
        resource_url = f"{PMC_AWS_BUCKET}/{pmcid}.{version}/{pmcid}.{version}.pdf"
        payload, metadata = self.client.get_bytes(resource_url)
        return resource_url, payload, metadata

    @staticmethod
    def _normalize_pmcid(value: str) -> str:
        normalized = str(value).strip().upper()
        if not re.fullmatch(r"PMC\d+", normalized):
            raise ValueError("pmcid must have the form PMC1234567.")
        return normalized

    @staticmethod
    def _parse_metadata(payload: bytes) -> dict[str, str]:
        root = ET.fromstring(payload)
        license_text = " ".join(
            " ".join(node.itertext()).strip()
            for node in root.findall(".//{*}license") + root.findall(".//{*}license-p")
            if " ".join(node.itertext()).strip()
        )
        article_title = " ".join(root.findtext(".//{*}article-title", default="").split())
        journal = " ".join(root.findtext(".//{*}journal-title", default="").split())
        attribution_parts = [part for part in (article_title, journal, "PMC Open Access") if part]
        return {
            "license": normalize_license(license_text),
            "license_text": license_text,
            "title": article_title,
            "journal": journal,
            "attribution": "; ".join(attribution_parts),
        }

    @staticmethod
    def _parse_oa_record(payload: bytes) -> dict[str, str | bool]:
        """Confirm the identifier is listed by PMC's Open Access service."""
        root = ET.fromstring(payload)
        record = root.find(".//record")
        if record is None:
            return {"is_open_access": False, "package_url": ""}
        link = record.find(".//link")
        return {
            "is_open_access": True,
            "package_url": str(link.get("href") or "") if link is not None else "",
        }

    @staticmethod
    def _parse_aws_versions(payload: bytes, pmcid: str) -> list[int]:
        root = ET.fromstring(payload)
        versions: list[int] = []
        pattern = re.compile(rf"{re.escape(pmcid)}\.(\d+)/")
        for node in root.findall(".//{*}CommonPrefixes/{*}Prefix"):
            match = pattern.fullmatch((node.text or "").strip())
            if match:
                versions.append(int(match.group(1)))
        return versions

    @staticmethod
    def _material_type(payload: bytes) -> tuple[str, str]:
        if payload.startswith(b"%PDF"):
            return ".pdf", "pdf"
        try:
            with Image.open(BytesIO(payload)) as image:
                image.verify()
                image_format = str(image.format or "").upper()
        except Exception:
            return "", ""
        extensions = {"PNG": ".png", "JPEG": ".jpg", "JPG": ".jpg"}
        return extensions.get(image_format, ""), image_format.lower()
