"""Project-wide configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent

ImageStrategy = Literal["original", "grayscale", "normalized", "binary"]
AppMode = Literal["demo", "production"]
StandardizationProfile = Literal["none", "conservative", "parent", "tautomer_canonical"]
CompareMode = Literal["raw", "standardized"]


def _env_text(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_lower(name: str, default: str) -> str:
    return _env_text(name, default).lower()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(str(raw if raw is not None else default).strip())
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    try:
        value = float(str(raw if raw is not None else default).strip())
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_path(name: str, default: str | Path, root: Path = PROJECT_ROOT) -> Path:
    configured = Path(os.getenv(name, str(default))).expanduser()
    return (configured if configured.is_absolute() else root / configured).resolve()


def _choice(value: str, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def _csv_tuple(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in os.getenv(name, default).split(",") if item.strip())


def _parse_reliability_weights(raw: str) -> dict[str, float]:
    weights: dict[str, float] = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        backend, value = item.split("=", 1)
        try:
            weights[backend.strip().lower()] = float(value.strip())
        except ValueError:
            continue
    return weights


@dataclass(frozen=True)
class Settings:
    """Validated runtime settings without filesystem side effects."""

    project_root: Path
    app_mode: AppMode
    data_dir: Path
    sample_dir: Path
    batch_input_dir: Path
    output_dir: Path
    app_db_path: Path
    runs_dir: Path
    model_dir: Path
    document_output_dir: Path
    run_retention_days: int
    run_max_storage_gb: float
    default_image_size: tuple[int, int]
    ocsr_backend: Literal["demo", "molscribe", "decimer", "ensemble"]
    ocsr_device: str
    ocsr_timeout_seconds: float
    ocsr_use_preprocessed_image: bool
    ocsr_strict_mode: bool
    ocsr_gpu_required: bool
    ocsr_gpu_max_concurrent_inference: int
    ocsr_gpu_allow_parallel_models: bool
    ocsr_fallback_image_strategies: tuple[str, ...]
    enable_admet_model: bool
    admet_model_path: Path
    molscribe_model_path: Path
    molscribe_model_name: str
    molscribe_model_version: str | None
    molscribe_isolated_subprocess: bool
    molscribe_image_strategy: ImageStrategy
    decimer_device: Literal["cpu", "gpu", "auto"]
    decimer_timeout_seconds: float
    decimer_image_strategy: ImageStrategy
    decimer_model_name: str
    decimer_model_version: str | None
    decimer_strict_mode: bool
    decimer_isolated_subprocess: bool
    ocsr_ensemble_backends: tuple[str, ...]
    ocsr_ensemble_backend_priority: tuple[str, ...]
    ocsr_ensemble_parallel: bool
    ocsr_ensemble_continue_on_error: bool
    ocsr_ensemble_total_timeout_seconds: float
    ocsr_ensemble_reliability_weights: dict[str, float]
    chem_standardization_profile: StandardizationProfile
    chem_standardization_compare_mode: CompareMode
    supported_image_extensions: set[str]
    supported_document_extensions: set[str]
    document_render_dpi: int
    document_max_file_size_mb: float
    document_max_pages: int
    document_max_pixels: int
    document_max_regions: int
    document_min_region_area: int
    document_max_region_area_ratio: float
    decision_accept_threshold: float
    decision_review_threshold: float
    decision_min_image_quality: float
    decision_require_calibrated_confidence: bool
    production_health_cache_enabled: bool
    production_health_load_model: bool
    production_health_warmup: bool
    production_health_cache_ttl_seconds: int
    production_health_warmup_input: Path


def load_settings() -> Settings:
    """Load environment settings, falling back safely for malformed values."""
    data_dir = PROJECT_ROOT / "data"
    output_dir = data_dir / "outputs"
    model_dir = PROJECT_ROOT / "models"
    molscribe_model_path = _env_path("MOLSCRIBE_MODEL_PATH", model_dir / "molscribe" / "swin_base_char_aux_1m.pth")
    admet_model_path = _env_path("ADMET_MODEL_PATH", model_dir / "admet_baseline.joblib")
    molscribe_strategy = _choice(_env_lower("MOLSCRIBE_IMAGE_STRATEGY", "original"), {"original", "grayscale", "normalized", "binary"}, "original")
    decimer_strategy = _choice(_env_lower("DECIMER_IMAGE_STRATEGY", "original"), {"original", "grayscale", "normalized", "binary"}, "original")
    standardization_profile = _choice(
        _env_lower("CHEM_STANDARDIZATION_PROFILE", "conservative"),
        {"none", "conservative", "parent", "tautomer_canonical"},
        "conservative",
    )
    compare_mode = _choice(_env_lower("CHEM_STANDARDIZATION_COMPARE_MODE", "raw"), {"raw", "standardized"}, "raw")
    app_mode = _choice(_env_lower("APP_MODE", "demo"), {"demo", "production"}, "demo")
    supported_images = {".png", ".jpg", ".jpeg"}
    return Settings(
        project_root=PROJECT_ROOT,
        app_mode=app_mode,  # type: ignore[arg-type]
        data_dir=data_dir,
        sample_dir=data_dir / "samples",
        batch_input_dir=data_dir / "batch_input",
        output_dir=output_dir,
        app_db_path=_env_path("APP_DB_PATH", data_dir / "app.db"),
        runs_dir=_env_path("RUNS_DIR", data_dir / "runs"),
        model_dir=model_dir,
        document_output_dir=_env_path("DOCUMENT_OUTPUT_DIR", output_dir / "documents"),
        run_retention_days=_env_int("RUN_RETENTION_DAYS", 30, minimum=1),
        run_max_storage_gb=_env_float("RUN_MAX_STORAGE_GB", 10.0, minimum=0.1),
        default_image_size=(512, 512),
        ocsr_backend=_choice(
            _env_lower("OCSR_BACKEND", "decimer" if app_mode == "production" else "demo"),
            {"demo", "molscribe", "decimer", "ensemble"},
            "decimer" if app_mode == "production" else "demo",
        ),  # type: ignore[arg-type]
        ocsr_device=_env_lower("OCSR_DEVICE", "auto"),
        ocsr_timeout_seconds=_env_float("OCSR_TIMEOUT_SECONDS", 120.0, minimum=0.0),
        ocsr_use_preprocessed_image=_env_bool("OCSR_USE_PREPROCESSED_IMAGE", False),
        ocsr_strict_mode=_env_bool("OCSR_STRICT_MODE", False),
        ocsr_gpu_required=_env_bool("OCSR_GPU_REQUIRED", False),
        ocsr_gpu_max_concurrent_inference=_env_int("OCSR_GPU_MAX_CONCURRENT_INFERENCE", 1, minimum=1),
        ocsr_gpu_allow_parallel_models=_env_bool("OCSR_GPU_ALLOW_PARALLEL_MODELS", False),
        ocsr_fallback_image_strategies=_csv_tuple("OCSR_FALLBACK_IMAGE_STRATEGIES", "original,enhanced,normalized,grayscale,binary"),
        enable_admet_model=_env_bool("ENABLE_ADMET_MODEL", False),
        admet_model_path=admet_model_path,
        molscribe_model_path=molscribe_model_path,
        molscribe_model_name=_env_text("MOLSCRIBE_MODEL_NAME", molscribe_model_path.name) or molscribe_model_path.name,
        molscribe_model_version=_env_text("MOLSCRIBE_MODEL_VERSION", "") or None,
        molscribe_isolated_subprocess=_env_bool("MOLSCRIBE_ISOLATED_SUBPROCESS", True),
        molscribe_image_strategy=molscribe_strategy,  # type: ignore[arg-type]
        decimer_device=_choice(_env_lower("DECIMER_DEVICE", _env_lower("OCSR_DEVICE", "auto")), {"cpu", "gpu", "auto"}, "auto"),  # type: ignore[arg-type]
        decimer_timeout_seconds=_env_float("DECIMER_TIMEOUT_SECONDS", _env_float("OCSR_TIMEOUT_SECONDS", 120.0, minimum=0.0), minimum=0.0),
        decimer_image_strategy=decimer_strategy,  # type: ignore[arg-type]
        decimer_model_name=_env_text("DECIMER_MODEL_NAME", "DECIMER Image Transformer") or "DECIMER Image Transformer",
        decimer_model_version=_env_text("DECIMER_MODEL_VERSION", "") or None,
        decimer_strict_mode=_env_bool("DECIMER_STRICT_MODE", _env_bool("OCSR_STRICT_MODE", False)),
        decimer_isolated_subprocess=_env_bool("DECIMER_ISOLATED_SUBPROCESS", True),
        ocsr_ensemble_backends=_csv_tuple("OCSR_ENSEMBLE_BACKENDS", "decimer,molscribe"),
        ocsr_ensemble_backend_priority=_csv_tuple("OCSR_ENSEMBLE_BACKEND_PRIORITY", "decimer,molscribe"),
        ocsr_ensemble_parallel=_env_bool("OCSR_ENSEMBLE_PARALLEL", False),
        ocsr_ensemble_continue_on_error=_env_bool("OCSR_ENSEMBLE_CONTINUE_ON_ERROR", True),
        ocsr_ensemble_total_timeout_seconds=_env_float("OCSR_ENSEMBLE_TOTAL_TIMEOUT_SECONDS", _env_float("OCSR_TIMEOUT_SECONDS", 240.0, minimum=0.0), minimum=0.0),
        ocsr_ensemble_reliability_weights=_parse_reliability_weights(os.getenv("OCSR_ENSEMBLE_RELIABILITY_WEIGHTS", "molscribe=1.0,decimer=1.0")),
        chem_standardization_profile=standardization_profile,  # type: ignore[arg-type]
        chem_standardization_compare_mode=compare_mode,  # type: ignore[arg-type]
        supported_image_extensions=supported_images,
        supported_document_extensions=supported_images | {".pdf", ".zip"},
        document_render_dpi=_env_int("DOCUMENT_RENDER_DPI", 200, minimum=1),
        document_max_file_size_mb=_env_float("DOCUMENT_MAX_FILE_SIZE_MB", 100.0, minimum=0.1),
        document_max_pages=_env_int("DOCUMENT_MAX_PAGES", 100, minimum=1),
        document_max_pixels=_env_int("DOCUMENT_MAX_PIXELS", 25000000, minimum=1),
        document_max_regions=_env_int("DOCUMENT_MAX_REGIONS", 500, minimum=1),
        document_min_region_area=_env_int("DOCUMENT_MIN_REGION_AREA", 1200, minimum=1),
        document_max_region_area_ratio=_env_float("DOCUMENT_MAX_REGION_AREA_RATIO", 0.80, minimum=0.01),
        decision_accept_threshold=_env_float("DECISION_ACCEPT_THRESHOLD", 0.85, minimum=0.0),
        decision_review_threshold=_env_float("DECISION_REVIEW_THRESHOLD", 0.65, minimum=0.0),
        decision_min_image_quality=_env_float("DECISION_MIN_IMAGE_QUALITY", 0.55, minimum=0.0),
        decision_require_calibrated_confidence=_env_bool(
            "DECISION_REQUIRE_CALIBRATED_CONFIDENCE",
            app_mode == "production",
        ),
        production_health_cache_enabled=_env_bool("PRODUCTION_HEALTH_CACHE_ENABLED", True),
        production_health_load_model=_env_bool("PRODUCTION_HEALTH_LOAD_MODEL", True),
        production_health_warmup=_env_bool("PRODUCTION_HEALTH_WARMUP", True),
        production_health_cache_ttl_seconds=_env_int("PRODUCTION_HEALTH_CACHE_TTL_SECONDS", 3600, minimum=0),
        production_health_warmup_input=_env_path("PRODUCTION_HEALTH_WARMUP_INPUT", data_dir / "samples" / "aspirin.png"),
    )


def validate_settings(settings: Settings) -> list[str]:
    """Return non-fatal configuration warnings."""
    warnings: list[str] = []
    if settings.document_max_region_area_ratio > 1.0:
        warnings.append("DOCUMENT_MAX_REGION_AREA_RATIO is greater than 1.0; region filtering may be ineffective.")
    if settings.app_mode == "production" and settings.ocsr_backend == "demo":
        warnings.append("APP_MODE=production forbids OCSR_BACKEND=demo for image recognition.")
    if settings.ocsr_backend == "molscribe" and not settings.molscribe_model_path.exists():
        warnings.append(f"MolScribe model file does not exist: {settings.molscribe_model_path}")
    return warnings


def initialize_directories(settings: Settings | None = None) -> None:
    """Create project-owned runtime directories explicitly."""
    active = settings or SETTINGS
    for directory in (
        active.data_dir,
        active.sample_dir,
        active.batch_input_dir,
        active.output_dir,
        active.runs_dir,
        active.model_dir,
        active.document_output_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


SETTINGS = load_settings()

DATA_DIR = SETTINGS.data_dir
APP_MODE = SETTINGS.app_mode
IS_PRODUCTION_MODE = SETTINGS.app_mode == "production"
SAMPLE_DIR = SETTINGS.sample_dir
BATCH_INPUT_DIR = SETTINGS.batch_input_dir
OUTPUT_DIR = SETTINGS.output_dir
APP_DB_PATH = SETTINGS.app_db_path
RUNS_DIR = SETTINGS.runs_dir
RUN_RETENTION_DAYS = SETTINGS.run_retention_days
RUN_MAX_STORAGE_GB = SETTINGS.run_max_storage_gb
MODEL_DIR = SETTINGS.model_dir
DEFAULT_IMAGE_SIZE = SETTINGS.default_image_size
OCSR_BACKEND = SETTINGS.ocsr_backend
OCSR_DEVICE = SETTINGS.ocsr_device
OCSR_TIMEOUT_SECONDS = SETTINGS.ocsr_timeout_seconds
OCSR_USE_PREPROCESSED_IMAGE = SETTINGS.ocsr_use_preprocessed_image
OCSR_STRICT_MODE = SETTINGS.ocsr_strict_mode
ENABLE_ADMET_MODEL = SETTINGS.enable_admet_model
ADMET_MODEL_PATH = SETTINGS.admet_model_path
MOLSCRIBE_MODEL_PATH = SETTINGS.molscribe_model_path
MOLSCRIBE_MODEL_NAME = SETTINGS.molscribe_model_name
MOLSCRIBE_MODEL_VERSION = SETTINGS.molscribe_model_version
MOLSCRIBE_ISOLATED_SUBPROCESS = SETTINGS.molscribe_isolated_subprocess
MOLSCRIBE_IMAGE_STRATEGY = SETTINGS.molscribe_image_strategy
DECIMER_DEVICE = SETTINGS.decimer_device
DECIMER_TIMEOUT_SECONDS = SETTINGS.decimer_timeout_seconds
DECIMER_IMAGE_STRATEGY = SETTINGS.decimer_image_strategy
DECIMER_MODEL_NAME = SETTINGS.decimer_model_name
DECIMER_MODEL_VERSION = SETTINGS.decimer_model_version
DECIMER_STRICT_MODE = SETTINGS.decimer_strict_mode
DECIMER_ISOLATED_SUBPROCESS = SETTINGS.decimer_isolated_subprocess
OCSR_ENSEMBLE_BACKENDS = SETTINGS.ocsr_ensemble_backends
OCSR_ENSEMBLE_BACKEND_PRIORITY = SETTINGS.ocsr_ensemble_backend_priority
OCSR_ENSEMBLE_PARALLEL = SETTINGS.ocsr_ensemble_parallel
OCSR_ENSEMBLE_CONTINUE_ON_ERROR = SETTINGS.ocsr_ensemble_continue_on_error
OCSR_ENSEMBLE_TOTAL_TIMEOUT_SECONDS = SETTINGS.ocsr_ensemble_total_timeout_seconds
OCSR_GPU_REQUIRED = SETTINGS.ocsr_gpu_required
OCSR_GPU_MAX_CONCURRENT_INFERENCE = SETTINGS.ocsr_gpu_max_concurrent_inference
OCSR_GPU_ALLOW_PARALLEL_MODELS = SETTINGS.ocsr_gpu_allow_parallel_models
OCSR_FALLBACK_IMAGE_STRATEGIES = SETTINGS.ocsr_fallback_image_strategies
OCSR_ENSEMBLE_RELIABILITY_WEIGHTS = SETTINGS.ocsr_ensemble_reliability_weights
CHEM_STANDARDIZATION_PROFILE = SETTINGS.chem_standardization_profile
CHEM_STANDARDIZATION_COMPARE_MODE = SETTINGS.chem_standardization_compare_mode
SUPPORTED_IMAGE_EXTENSIONS = SETTINGS.supported_image_extensions
SUPPORTED_DOCUMENT_EXTENSIONS = SETTINGS.supported_document_extensions
DOCUMENT_OUTPUT_DIR = SETTINGS.document_output_dir
DOCUMENT_RENDER_DPI = SETTINGS.document_render_dpi
DOCUMENT_MAX_FILE_SIZE_MB = SETTINGS.document_max_file_size_mb
DOCUMENT_MAX_PAGES = SETTINGS.document_max_pages
DOCUMENT_MAX_PIXELS = SETTINGS.document_max_pixels
DOCUMENT_MAX_REGIONS = SETTINGS.document_max_regions
DOCUMENT_MIN_REGION_AREA = SETTINGS.document_min_region_area
DOCUMENT_MAX_REGION_AREA_RATIO = SETTINGS.document_max_region_area_ratio
DECISION_ACCEPT_THRESHOLD = SETTINGS.decision_accept_threshold
DECISION_REVIEW_THRESHOLD = SETTINGS.decision_review_threshold
DECISION_MIN_IMAGE_QUALITY = SETTINGS.decision_min_image_quality
DECISION_REQUIRE_CALIBRATED_CONFIDENCE = SETTINGS.decision_require_calibrated_confidence
PRODUCTION_HEALTH_CACHE_ENABLED = SETTINGS.production_health_cache_enabled
PRODUCTION_HEALTH_LOAD_MODEL = SETTINGS.production_health_load_model
PRODUCTION_HEALTH_WARMUP = SETTINGS.production_health_warmup
PRODUCTION_HEALTH_CACHE_TTL_SECONDS = SETTINGS.production_health_cache_ttl_seconds
PRODUCTION_HEALTH_WARMUP_INPUT = SETTINGS.production_health_warmup_input
