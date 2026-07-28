"""Tests for the no-model demonstration adapter."""

from src.ocsr.demo_adapter import DemoOCSRAdapter


def test_demo_adapter_recognizes_aspirin_filename() -> None:
    result = DemoOCSRAdapter().recognize("my_aspirin_scan.png")
    assert result.status == "success"
    assert result.smiles == "CC(=O)OC1=CC=CC=C1C(=O)O"
    assert result.confidence == 0.95


def test_demo_adapter_fails_helpfully_for_unknown_name() -> None:
    result = DemoOCSRAdapter().recognize("unknown.png")
    assert result.status == "failed"
    assert result.smiles is None
    assert "MolScribe/DECIMER" in result.message
