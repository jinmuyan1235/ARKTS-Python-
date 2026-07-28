"""PubChem PUG REST collector for auditable public-domain structure records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.chem.smiles_validator import validate_smiles
from src.datasets.http import CachedHttpClient
from src.datasets.licenses import PUBCHEM_PUBLIC_DOMAIN
from src.datasets.provenance import SourceRecord, sha256_bytes, utc_now_iso
from src.utils.file_utils import ensure_directory


PUG_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid"


@dataclass(frozen=True)
class PubChemStructure:
    cid: int
    smiles: str
    canonical_smiles: str
    inchikey: str
    sdf_path: Path
    image_path: Path
    source: SourceRecord


class PubChemCollector:
    """Collect a CID's descriptor, 2D SDF and PNG from the public PUG API."""

    def __init__(self, client: CachedHttpClient, material_root: str | Path) -> None:
        self.client = client
        self.material_root = ensure_directory(Path(material_root).expanduser().resolve())

    def collect(self, cid: int, *, dry_run: bool = False) -> PubChemStructure | SourceRecord:
        cid = int(cid)
        property_url = f"{PUG_BASE}/{cid}/property/CanonicalSMILES,IsomericSMILES,InChIKey/JSON"
        properties_payload, property_metadata = self.client.get_bytes(property_url)
        properties = self._parse_properties(properties_payload, cid)
        source = SourceRecord(
            source_key=f"pubchem:{cid}",
            source_kind="pubchem",
            source_id=str(cid),
            source_url=property_url,
            license=PUBCHEM_PUBLIC_DOMAIN,
            license_allowed=True,
            retrieved_at=utc_now_iso(),
            source_sha256=property_metadata["sha256"],
            attribution=f"PubChem CID {cid}; public-domain data supplied by PubChem.",
            metadata={"cid": cid, "property_url": property_url, **properties},
        )
        if dry_run:
            return source

        target = ensure_directory(self.material_root / "pubchem" / str(cid))
        sdf_payload, _sdf_metadata = self.client.get_bytes(f"{PUG_BASE}/{cid}/record/SDF?record_type=2d")
        png_payload, png_metadata = self.client.get_bytes(f"{PUG_BASE}/{cid}/PNG?image_size=1000x1000")
        sdf_path = target / f"CID_{cid}.sdf"
        image_path = target / f"CID_{cid}.png"
        sdf_path.write_bytes(sdf_payload)
        image_path.write_bytes(png_payload)
        source = SourceRecord(
            **{
                **source.__dict__,
                "metadata": {
                    **(source.metadata or {}),
                    "sdf_url": f"{PUG_BASE}/{cid}/record/SDF?record_type=2d",
                    "sdf_sha256": sha256_bytes(sdf_payload),
                    "sdf_path": str(sdf_path),
                    "png_url": f"{PUG_BASE}/{cid}/PNG?image_size=1000x1000",
                    "png_sha256": png_metadata["sha256"],
                    "png_path": str(image_path),
                },
            }
        )
        return PubChemStructure(
            cid=cid,
            smiles=properties["smiles"],
            canonical_smiles=properties["canonical_smiles"],
            inchikey=properties["inchikey"],
            sdf_path=sdf_path,
            image_path=image_path,
            source=source,
        )

    @staticmethod
    def _parse_properties(payload: bytes, cid: int) -> dict[str, str]:
        data = json.loads(payload.decode("utf-8"))
        properties = (data.get("PropertyTable") or {}).get("Properties") or []
        row = next((item for item in properties if int(item.get("CID", -1)) == cid), None)
        if row is None:
            raise ValueError(f"PubChem property response does not contain CID {cid}.")
        smiles = str(row.get("SMILES") or row.get("IsomericSMILES") or row.get("ConnectivitySMILES") or "").strip()
        validation = validate_smiles(smiles)
        if not validation["valid"]:
            raise ValueError(f"PubChem CID {cid} returned invalid SMILES: {validation.get('error')}")
        return {
            "smiles": smiles,
            "canonical_smiles": str(validation.get("canonical_smiles") or ""),
            "inchikey": str(row.get("InChIKey") or ""),
        }
