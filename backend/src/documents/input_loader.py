"""Input expansion and optional PDF rendering for document OCSR."""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

from PIL import Image

import config
from src.documents.models import DocumentPage
from src.utils.file_utils import ensure_directory, safe_stem


class DocumentInputError(ValueError):
    """Raised when a document input violates safety or format constraints."""


class OptionalDependencyError(DocumentInputError):
    """Raised when an optional renderer dependency is unavailable."""


def document_id_for(path: str | Path) -> str:
    """Create a stable document id from the input path and a short content hash."""
    source = Path(path).expanduser().resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    return f"{safe_stem(source.stem, 'document')}_{digest}"


def check_file_size(path: str | Path, max_size_mb: float = config.DOCUMENT_MAX_FILE_SIZE_MB) -> None:
    """Reject very large uploads before decoding them."""
    source = Path(path).expanduser().resolve()
    size_mb = source.stat().st_size / (1024 * 1024)
    if size_mb > max_size_mb:
        raise DocumentInputError(f"Input file is {size_mb:.1f} MB, above the {max_size_mb:.1f} MB safety limit.")


def check_image_limits(path: str | Path, max_pixels: int = config.DOCUMENT_MAX_PIXELS) -> tuple[int, int]:
    """Return image size after checking decode and pixel limits."""
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        raise DocumentInputError(f"Image is damaged or unsupported: {exc}") from exc
    if width * height > max_pixels:
        raise DocumentInputError(f"Image has {width * height} pixels, above the {max_pixels} pixel safety limit.")
    return width, height


def _copy_page_image(source: Path, destination: Path) -> tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    width, height = check_image_limits(source)
    shutil.copy2(source, destination)
    return width, height


class PDFRenderer:
    """Lazy PyMuPDF renderer used only when PDF input is requested."""

    dependency_note = (
        "PyMuPDF is an optional dependency. Its upstream project is distributed under AGPL/commercial licensing; "
        "install it only when that licensing fits your deployment."
    )

    def __init__(self, dpi: int = config.DOCUMENT_RENDER_DPI) -> None:
        self.dpi = dpi

    def _load_fitz(self):
        try:
            import fitz  # type: ignore
        except (ImportError, ModuleNotFoundError) as exc:
            raise OptionalDependencyError(
                "PDF rendering requires optional dependency PyMuPDF (`pip install pymupdf`). "
                + self.dependency_note
            ) from exc
        return fitz

    def render(self, pdf_path: str | Path, output_dir: str | Path, document_id: str) -> list[DocumentPage]:
        """Render a PDF into page PNG files with readable errors."""
        source = Path(pdf_path).expanduser().resolve()
        check_file_size(source)
        fitz = self._load_fitz()
        try:
            document = fitz.open(str(source))
        except Exception as exc:
            raise DocumentInputError(f"PDF is damaged, encrypted, or unsupported: {exc}") from exc
        try:
            if getattr(document, "needs_pass", False):
                raise DocumentInputError("Password-protected PDFs are not supported.")
            page_count = int(getattr(document, "page_count", len(document)))
            if page_count > config.DOCUMENT_MAX_PAGES:
                raise DocumentInputError(
                    f"PDF 共 {page_count} 页，超过当前全文处理上限 {config.DOCUMENT_MAX_PAGES} 页。"
                    "如确需处理更长文档，请提高环境变量 DOCUMENT_MAX_PAGES 后重启应用。"
                )
            pages: list[DocumentPage] = []
            page_dir = ensure_directory(Path(output_dir) / "pages")
            zoom = self.dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            for index in range(page_count):
                page = document.load_page(index)
                text_boxes = self._extract_text_boxes(page, zoom)
                figure_boxes = self._extract_figure_boxes(page, zoom)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                width, height = int(pixmap.width), int(pixmap.height)
                if width * height > config.DOCUMENT_MAX_PIXELS:
                    raise DocumentInputError(
                        f"Rendered page {index + 1} has {width * height} pixels, above the safety limit."
                    )
                page_path = page_dir / f"{document_id}_p{index + 1:03d}.png"
                pixmap.save(str(page_path))
                pages.append(DocumentPage(
                    document_id=document_id,
                    page_number=index + 1,
                    image_path=str(page_path.resolve()),
                    width=width,
                    height=height,
                    source_path=str(source),
                    source_type="pdf",
                    render_dpi=self.dpi,
                    page_label=f"p{index + 1:03d}",
                    text_boxes=text_boxes,
                    figure_boxes=figure_boxes,
                ))
            return pages
        finally:
            close = getattr(document, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _extract_text_boxes(page, zoom: float) -> list[dict]:
        """Return PDF text-layer words in rendered-page pixel coordinates."""

        try:
            words = page.get_text("words") or []
        except Exception:
            return []
        boxes: list[dict] = []
        for word in words:
            if len(word) < 5:
                continue
            text = str(word[4] or "").strip()
            if not text:
                continue
            x1, y1, x2, y2 = [int(round(float(value) * zoom)) for value in word[:4]]
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append({"bbox": [x1, y1, x2, y2], "text": text, "source": "pdf_text_layer"})
        return boxes

    @staticmethod
    def _extract_figure_boxes(page, zoom: float) -> list[dict]:
        """Return embedded PDF image bounds for figure-label context checks."""

        try:
            images = page.get_image_info() or []
        except Exception:
            return []
        boxes: list[dict] = []
        for image in images:
            bbox = image.get("bbox") if isinstance(image, dict) else None
            if not bbox or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = [int(round(float(value) * zoom)) for value in bbox]
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append({"bbox": [x1, y1, x2, y2], "source": "pdf_image_layer"})
        return boxes


class DocumentInputLoader:
    """Expand PDF, page-image, and ZIP inputs into rendered page images."""

    def __init__(self, output_dir: str | Path, renderer: PDFRenderer | None = None) -> None:
        self.output_dir = ensure_directory(output_dir)
        self.renderer = renderer or PDFRenderer()

    def load(self, input_path: str | Path) -> tuple[str, list[DocumentPage]]:
        """Load a supported document input into page records."""
        source = Path(input_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Input document does not exist: {source}")
        check_file_size(source)
        suffix = source.suffix.lower()
        if suffix not in config.SUPPORTED_DOCUMENT_EXTENSIONS:
            raise DocumentInputError(f"Unsupported input type: {suffix}. Use PDF, PNG/JPG/JPEG, or ZIP.")
        document_id = document_id_for(source)
        document_dir = ensure_directory(self.output_dir / document_id)
        if suffix == ".pdf":
            return document_id, self.renderer.render(source, document_dir, document_id)
        if suffix == ".zip":
            return document_id, self._load_zip(source, document_dir, document_id)
        return document_id, [self._load_image_page(source, document_dir, document_id, 1)]

    def _load_image_page(self, source: Path, document_dir: Path, document_id: str, page_number: int) -> DocumentPage:
        page_path = document_dir / "pages" / f"{document_id}_p{page_number:03d}{source.suffix.lower()}"
        width, height = _copy_page_image(source, page_path)
        return DocumentPage(
            document_id=document_id,
            page_number=page_number,
            image_path=str(page_path.resolve()),
            width=width,
            height=height,
            source_path=str(source),
            source_type="image",
            page_label=f"p{page_number:03d}",
        )

    def _load_zip(self, source: Path, document_dir: Path, document_id: str) -> list[DocumentPage]:
        try:
            archive = zipfile.ZipFile(source)
        except zipfile.BadZipFile as exc:
            raise DocumentInputError(f"ZIP archive is damaged: {exc}") from exc
        pages: list[DocumentPage] = []
        extract_dir = ensure_directory(document_dir / "zip_input")
        with archive:
            image_members = [
                info for info in archive.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() in config.SUPPORTED_IMAGE_EXTENSIONS
            ]
            if len(image_members) > config.DOCUMENT_MAX_PAGES:
                raise DocumentInputError(
                    f"ZIP 共包含 {len(image_members)} 张页面图片，超过当前全文处理上限 "
                    f"{config.DOCUMENT_MAX_PAGES} 页。"
                )
            for index, info in enumerate(sorted(image_members, key=lambda item: item.filename.lower()), start=1):
                member_name = Path(info.filename).name
                if not member_name:
                    continue
                extracted = extract_dir / f"{index:03d}_{safe_stem(Path(member_name).stem)}{Path(member_name).suffix.lower()}"
                with archive.open(info) as reader:
                    extracted.write_bytes(reader.read())
                page = self._load_image_page(extracted, document_dir, document_id, index)
                page.source_type = "zip_image"
                page.source_path = f"{source}!{info.filename}"
                pages.append(page)
        if not pages:
            raise DocumentInputError("ZIP archive does not contain supported page images.")
        return pages


def iter_page_paths(pages: Iterable[DocumentPage]) -> Iterable[Path]:
    """Yield page image paths from page records."""
    for page in pages:
        yield Path(page.image_path)
