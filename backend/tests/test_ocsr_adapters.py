"""Compatibility tests for optional backend result normalization."""

from pathlib import Path

from src.ocsr.decimer_adapter import DECIMERAdapter
from src.ocsr.demo_adapter import DemoOCSRAdapter
from src.ocsr.molscribe_adapter import MolScribeAdapter
from src.ocsr.recognizer import MoleculeRecognizer, ProductionModeError


def test_demo_backend_reports_available() -> None:
    status = DemoOCSRAdapter().status()
    assert status["backend"] == "demo"
    assert status["available"] is True


def test_production_mode_blocks_demo_recognizer(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "APP_MODE", "production")
    try:
        MoleculeRecognizer("demo")
    except ProductionModeError:
        pass
    else:
        raise AssertionError("Expected demo backend to be blocked in production mode")


def test_recognizer_uses_configured_molscribe_device_by_default(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "OCSR_DEVICE", "cuda:0")
    recognizer = MoleculeRecognizer("molscribe")
    assert recognizer.adapter.device == "cuda:0"


def test_recognizer_passes_default_devices_to_ensemble(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "OCSR_DEVICE", "cuda:0")
    monkeypatch.setattr(config, "DECIMER_DEVICE", "gpu")
    recognizer = MoleculeRecognizer("ensemble")
    assert recognizer.adapter.runtime_config["molscribe_device"] == "cuda:0"
    assert recognizer.adapter.runtime_config["decimer_device"] == "gpu"


def test_decimer_tuple_result_with_confidence(tmp_path: Path) -> None:
    image_path = tmp_path / "molecule.png"
    image_path.write_bytes(b"fake-image")
    adapter = DECIMERAdapter()
    adapter.predictor = lambda _path, confidence=True: ("CCO", 0.88)
    result = adapter.recognize(image_path)
    assert result.status == "success"
    assert result.smiles == "CCO"
    assert result.confidence == 0.88


def test_molscribe_dict_result_with_confidence(tmp_path: Path) -> None:
    class FakeModel:
        def predict_image_file(self, _path: str, return_confidence: bool = True) -> dict:
            return {"smiles": "CCO", "confidence": 0.91}

    image_path = tmp_path / "molecule.png"
    image_path.write_bytes(b"fake-image")
    adapter = MolScribeAdapter()
    adapter.model = FakeModel()
    adapter.import_error = None
    result = adapter.recognize(image_path)
    assert result.status == "success"
    assert result.confidence == 0.91
