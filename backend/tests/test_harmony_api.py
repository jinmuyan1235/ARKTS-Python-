"""Integration coverage for the HarmonyOS local API."""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import re
import shutil
import time
from threading import Event
from types import SimpleNamespace
from typing import Iterator

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import api_server
from src.analysis.correction import apply_smiles_correction, save_correction_feedback
from src.analysis.molecule_report import MoleculeReportGenerator
from src.storage.analysis_repository import AnalysisRepository
from src.storage.auth_repository import AuthRepository


API_KEY = "harmony-test-secret"
API_HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture()
def harmony_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Path, dict[str, str]]]:
    runs_dir = tmp_path / "runs"
    data_dir = tmp_path / "data"
    repository = AnalysisRepository(tmp_path / "history.db")
    auth_repository = AuthRepository(tmp_path / "history.db")
    monkeypatch.setenv("HARMONY_API_KEY", API_KEY)
    monkeypatch.setattr(api_server.config, "APP_MODE", "demo")
    monkeypatch.setattr(api_server.config, "OCSR_BACKEND", "demo")
    monkeypatch.setattr(api_server.config, "IS_PRODUCTION_MODE", False)
    monkeypatch.setattr(api_server.config, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(api_server.config, "DATA_DIR", data_dir)
    monkeypatch.setattr(api_server.config, "DOCUMENT_OUTPUT_DIR", tmp_path / "documents")
    monkeypatch.setattr(api_server, "AnalysisRepository", lambda: repository)
    monkeypatch.setattr(api_server, "AuthRepository", lambda: auth_repository)
    monkeypatch.setattr(
        api_server,
        "record_report",
        lambda report, report_path=None, owner_user_id=None: repository.save_analysis(
            report,
            report_path,
            owner_user_id=owner_user_id,
        ),
    )
    with TestClient(api_server.app) as client:
        registered = client.post(
            "/api/v1/auth/register",
            headers=API_HEADERS,
            json={
                "username": "researcher",
                "displayName": "测试研究员",
                "password": "safe-password-123",
                "role": "算法组",
            },
        )
        assert registered.status_code == 201
        auth_headers = {
            **API_HEADERS,
            "Authorization": f"Bearer {registered.json()['token']}",
        }
        yield client, runs_dir, auth_headers


def _wait_for_job(client: TestClient, job_id: str, auth_headers: dict[str, str]) -> dict:
    for _attempt in range(100):
        response = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError("background image job did not finish")


def test_document_detection_edit_and_human_confirmation_gate(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    client, _runs_dir, auth_headers = harmony_client
    document_output = Path(api_server.config.DOCUMENT_OUTPUT_DIR) / "document-api-result"
    document_output.mkdir(parents=True)

    class FakeDocumentProcessor:
        def __init__(self) -> None:
            self.report_generator = SimpleNamespace(output_dir=document_output)
            self.recognize_calls = 0

        def process(self, input_path: str, run_ocsr: bool = False) -> dict:
            assert run_ocsr is False
            page_path = document_output / "page-001.png"
            shutil.copy2(input_path, page_path)
            return {
                "document_id": "fixture-document",
                "created_at": "2026-07-28T00:00:00+00:00",
                "input_path": input_path,
                "output_dir": str(document_output),
                "backend": "demo",
                "detector": "fixture",
                "pages": [{
                    "document_id": "fixture-document", "page_number": 1,
                    "image_path": str(page_path), "width": 320, "height": 240,
                }],
                "regions": [{
                    "document_id": "fixture-document", "page_number": 1,
                    "region_id": "p001_r001", "bbox": [20, 20, 180, 150],
                    "region_type": "reaction_like", "status": "detected",
                    "confirmed": False, "audit": [], "screening": {}, "review": {},
                    "ocsr": {}, "final_result": {}, "report": None,
                }],
                "detection_errors": [],
                "summary": {"page_count": 1, "region_count": 1},
                "exports": {},
            }

    fake = FakeDocumentProcessor()
    api_server.app.state.document_processor = fake
    image = BytesIO()
    Image.new("RGB", (320, 240), "white").save(image, format="PNG")
    uploaded = client.post(
        "/api/v1/documents",
        headers=auth_headers,
        files={"file": ("picker-result", image.getvalue(), "application/octet-stream")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["contentType"] == "image/png"
    assert uploaded.json()["filename"].endswith(".png")
    document_id = uploaded.json()["documentId"]

    detected = client.post(f"/api/v1/documents/{document_id}/detect", headers=auth_headers)
    assert detected.status_code == 202
    assert _wait_for_job(client, detected.json()["jobId"], auth_headers)["status"] == "completed"
    document = client.get(f"/api/v1/documents/{document_id}", headers=auth_headers)
    assert document.status_code == 200
    assert document.json()["pageCount"] == 1
    assert client.get(document.json()["pages"][0]["previewUrl"]).status_code == 200

    reaction = client.post(
        f"/api/v1/documents/{document_id}/regions/p001_r001/recognize",
        headers=auth_headers,
        json={"confirmed": True},
    )
    assert reaction.status_code == 409
    assert "reaction_like" in reaction.json()["detail"]
    assert fake.recognize_calls == 0

    edited = client.patch(
        f"/api/v1/documents/{document_id}/regions/p001_r001",
        headers=auth_headers,
        json={
            "bbox": [25, 25, 170, 145],
            "regionType": "molecule",
            "confirmed": False,
            "note": "人工调整边界",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["regionType"] == "molecule"
    assert edited.json()["confirmed"] is False
    unconfirmed = client.post(
        f"/api/v1/documents/{document_id}/regions/p001_r001/recognize",
        headers=auth_headers,
        json={"confirmed": False},
    )
    assert unconfirmed.status_code == 422


def test_api_key_missing_wrong_and_correct(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, _runs_dir, auth_headers = harmony_client
    assert client.get("/api/v1/health").status_code == 401
    assert client.get("/api/v1/health", headers={"X-API-Key": "wrong"}).status_code == 401
    response = client.get("/api/v1/health", headers=API_HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert client.get("/api/v1/samples", headers=API_HEADERS).status_code == 401
    assert client.get("/api/v1/samples", headers=auth_headers).status_code == 200


def test_unicode_configured_api_key_rejects_without_server_error(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _runs_dir, _auth_headers = harmony_client
    monkeypatch.setenv("HARMONY_API_KEY", "本地分子服务-2026")
    wrong = client.get("/api/v1/health", headers={"X-API-Key": API_KEY})
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "API 密钥错误或缺失。"


def test_register_login_logout_members_and_avatar(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, _runs_dir, auth_headers = harmony_client
    duplicate = client.post(
        "/api/v1/auth/register",
        headers=API_HEADERS,
        json={
            "username": "RESEARCHER",
            "displayName": "重复用户",
            "password": "another-password",
        },
    )
    assert duplicate.status_code == 409
    assert client.post(
        "/api/v1/auth/login",
        headers=API_HEADERS,
        json={"username": "researcher", "password": "wrong-password"},
    ).status_code == 401
    login = client.post(
        "/api/v1/auth/login",
        headers=API_HEADERS,
        json={"username": "researcher", "password": "safe-password-123"},
    )
    assert login.status_code == 200
    login_headers = {
        **API_HEADERS,
        "Authorization": f"Bearer {login.json()['token']}",
    }
    assert client.get("/api/v1/auth/me", headers=login_headers).json()["displayName"] == "测试研究员"

    avatar_buffer = BytesIO()
    Image.new("RGB", (80, 60), color=(15, 127, 140)).save(avatar_buffer, format="PNG")
    avatar = client.post(
        "/api/v1/auth/me/avatar",
        headers=login_headers,
        files={"file": ("avatar.png", avatar_buffer.getvalue(), "image/png")},
    )
    assert avatar.status_code == 200
    assert avatar.json()["avatarUrl"]
    assert client.get(avatar.json()["avatarUrl"]).status_code == 200
    members = client.get("/api/v1/auth/members", headers=auth_headers)
    assert members.status_code == 200
    assert members.json()[0]["avatarUrl"]
    public_members = client.get("/api/v1/auth/members", headers=API_HEADERS)
    assert public_members.status_code == 200
    assert public_members.json()[0]["username"] == "researcher"

    assert client.post("/api/v1/auth/logout", headers=login_headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=login_headers).status_code == 401


def test_product_user_and_developer_accounts_have_separate_access(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, _runs_dir, developer_headers = harmony_client
    registered = client.post(
        "/api/v1/auth/register",
        headers=API_HEADERS,
        json={
            "username": "product-user",
            "displayName": "普通使用者",
            "password": "product-password-123",
            "role": "架构统筹",
            "accountType": "user",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["user"]["accountType"] == "user"
    assert registered.json()["user"]["role"] == ""
    user_headers = {
        **API_HEADERS,
        "Authorization": f"Bearer {registered.json()['token']}",
    }

    wrong_portal = client.post(
        "/api/v1/auth/login",
        headers=API_HEADERS,
        json={
            "username": "product-user",
            "password": "product-password-123",
            "accountType": "developer",
        },
    )
    assert wrong_portal.status_code == 403
    assert "开发者" in wrong_portal.json()["detail"]
    user_login = client.post(
        "/api/v1/auth/login",
        headers=API_HEADERS,
        json={
            "username": "product-user",
            "password": "product-password-123",
            "accountType": "user",
        },
    )
    assert user_login.status_code == 200

    members = client.get("/api/v1/auth/members", headers=developer_headers)
    assert members.status_code == 200
    assert all(member["accountType"] == "developer" for member in members.json())
    assert "product-user" not in {member["username"] for member in members.json()}
    assert client.get("/api/v1/feedback", headers=user_headers).status_code == 403
    assert client.post("/api/v1/documents", headers=user_headers).status_code == 403

    developer_result = client.post(
        "/api/v1/analyze-smiles", headers=developer_headers, json={"smiles": "CCO"}
    ).json()
    user_result = client.post(
        "/api/v1/analyze-smiles", headers=user_headers, json={"smiles": "CCN"}
    ).json()
    user_history = client.get("/api/v1/analyses?scope=all", headers=user_headers)
    assert user_history.status_code == 200
    assert {item["analysisId"] for item in user_history.json()["items"]} == {user_result["analysisId"]}
    foreign_owner = developer_result["createdBy"]["userId"]
    assert client.get(
        f"/api/v1/analyses?ownerUserId={foreign_owner}", headers=user_headers
    ).status_code == 403
    assert client.get(
        f"/api/v1/analyses/{developer_result['analysisId']}", headers=user_headers
    ).status_code in {403, 404}


def test_serial_job_store_pause_resume_and_cancel(tmp_path: Path) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    store = api_server.JobStore(tmp_path / "jobs", executor)
    started = Event()
    release = Event()

    def blocking_task() -> dict:
        started.set()
        assert release.wait(timeout=3)
        return {}

    running = store.create("test", "running-analysis", owner_user_id="owner")
    store.submit_task(running, blocking_task, "running", "completed")
    assert started.wait(timeout=2)
    assert store.pause(running["jobId"])["status"] == "pausing"
    assert store.resume(running["jobId"])["status"] == "running"
    assert store.cancel(running["jobId"])["status"] == "cancelling"
    release.set()
    for _attempt in range(50):
        if store.load(running["jobId"])["status"] == "cancelled":
            break
        time.sleep(0.02)
    assert store.load(running["jobId"])["status"] == "cancelled"

    queue_gate = Event()
    queue_started = Event()

    def queue_blocker() -> dict:
        queue_started.set()
        assert queue_gate.wait(timeout=3)
        return {}

    first = store.create("test", "first", owner_user_id="owner")
    second = store.create("test", "second", owner_user_id="owner")
    store.submit_task(first, queue_blocker, "running", "completed")
    assert queue_started.wait(timeout=2)
    store.submit_task(second, lambda: {}, "running", "completed")
    assert store.pause(second["jobId"])["status"] == "paused"
    assert store.resume(second["jobId"])["status"] == "queued"
    assert store.cancel(second["jobId"])["status"] == "cancelled"
    queue_gate.set()
    executor.shutdown(wait=True)


def test_profile_update_and_password_change_rotates_sessions(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, _runs_dir, auth_headers = harmony_client
    second_login = client.post(
        "/api/v1/auth/login",
        headers=API_HEADERS,
        json={"username": "researcher", "password": "safe-password-123"},
    )
    old_second_headers = {
        **API_HEADERS,
        "Authorization": f"Bearer {second_login.json()['token']}",
    }
    updated = client.put(
        "/api/v1/auth/me",
        headers=auth_headers,
        json={"displayName": "Molecule Lead", "role": "算法组"},
    )
    assert updated.status_code == 200
    assert updated.json()["displayName"] == "Molecule Lead"
    assert updated.json()["username"] == "researcher"

    wrong = client.post(
        "/api/v1/auth/change-password",
        headers=auth_headers,
        json={"oldPassword": "wrong-password", "newPassword": "new-safe-password-456"},
    )
    assert wrong.status_code == 422
    changed = client.post(
        "/api/v1/auth/change-password",
        headers=auth_headers,
        json={
            "oldPassword": "safe-password-123",
            "newPassword": "new-safe-password-456",
        },
    )
    assert changed.status_code == 200
    new_headers = {
        **API_HEADERS,
        "Authorization": f"Bearer {changed.json()['token']}",
    }
    assert client.get("/api/v1/auth/me", headers=auth_headers).status_code == 401
    assert client.get("/api/v1/auth/me", headers=old_second_headers).status_code == 401
    assert client.get("/api/v1/auth/me", headers=new_headers).status_code == 200
    assert client.post(
        "/api/v1/auth/login",
        headers=API_HEADERS,
        json={"username": "researcher", "password": "safe-password-123"},
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        headers=API_HEADERS,
        json={"username": "researcher", "password": "new-safe-password-456"},
    ).status_code == 200


def test_smiles_history_favorite_and_index_only_delete(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, runs_dir, auth_headers = harmony_client
    invalid = client.post("/api/v1/analyze-smiles", headers=auth_headers, json={"smiles": "C("})
    assert invalid.status_code == 422

    created = client.post("/api/v1/analyze-smiles", headers=auth_headers, json={"smiles": "CCO"})
    assert created.status_code == 200
    result = created.json()
    analysis_id = result["analysisId"]
    report_path = runs_dir / analysis_id / "report.json"
    assert result["inputType"] == "smiles"
    assert result["needsReview"] is False
    assert result["sourceImageUrl"] is None
    assert result["identity"]["inchiKey"]
    assert result["identity"]["heavyAtomCount"] == 3
    assert result["lipinskiDetail"]["checks"]
    assert result["review"]["status"] == "not_required"
    assert result["createdBy"]["username"] == "researcher"
    assert report_path.is_file()

    history = client.get("/api/v1/analyses?query=CCO", headers=auth_headers)
    assert history.status_code == 200
    assert any(item["analysisId"] == analysis_id for item in history.json()["items"])

    favorite = client.patch(
        f"/api/v1/analyses/{analysis_id}/favorite",
        headers=auth_headers,
        json={"favorite": True},
    )
    assert favorite.status_code == 200
    detail = client.get(f"/api/v1/analyses/{analysis_id}", headers=auth_headers)
    assert detail.json()["isFavorite"] is True

    deleted = client.delete(f"/api/v1/analyses/{analysis_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert report_path.is_file(), "deleting the SQLite index must retain report artifacts"
    assert client.get(f"/api/v1/analyses/{analysis_id}", headers=auth_headers).status_code == 404


def test_single_export_center_signed_downloads_and_confirmation_gate(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, runs_dir, auth_headers = harmony_client
    manual = client.post("/api/v1/analyze-smiles", headers=auth_headers, json={"smiles": "CCO"})
    assert manual.status_code == 200
    manual_id = manual.json()["analysisId"]
    center = client.get(f"/api/v1/analyses/{manual_id}/exports", headers=auth_headers)
    assert center.status_code == 200
    items = {item["format"]: item for item in center.json()["items"]}
    assert set(items) == {"csv", "json", "pdf", "smi", "mol", "sdf", "zip"}
    assert all(item["available"] for item in items.values())
    assert center.json()["expiresInSeconds"] == api_server.MOBILE_EXPORT_TTL_SECONDS

    downloaded = {export_format: client.get(item["downloadUrl"]) for export_format, item in items.items()}
    assert all(response.status_code == 200 for response in downloaded.values())
    assert downloaded["pdf"].content.startswith(b"%PDF")
    assert downloaded["zip"].content.startswith(b"PK")
    assert str(runs_dir).encode() not in downloaded["json"].content
    bad_url = items["csv"]["downloadUrl"].rsplit("token=", 1)[0] + "token=wrong"
    assert client.get(bad_url).status_code == 401
    # Replace the actual expiry without needing to know the server clock.
    expired_url = re.sub(r"expires=\d+", "expires=1", items["csv"]["downloadUrl"])
    assert client.get(expired_url).status_code == 410
    direct = client.get(f"/api/v1/analyses/{manual_id}/exports/pdf", headers=auth_headers)
    assert direct.status_code == 200
    assert direct.json()["format"] == "pdf"
    assert client.get(direct.json()["downloadUrl"]).content.startswith(b"%PDF")

    sample = client.post("/api/v1/jobs/samples/aspirin", headers=auth_headers).json()
    candidate = _wait_for_job(client, sample["jobId"], auth_headers)
    candidate_id = candidate["analysisId"]
    candidate_center = client.get(f"/api/v1/analyses/{candidate_id}/exports", headers=auth_headers)
    candidate_items = {item["format"]: item for item in candidate_center.json()["items"]}
    assert candidate_items["csv"]["available"] is True
    assert candidate_items["pdf"]["available"] is True
    assert candidate_items["smi"]["available"] is False
    assert candidate_items["sdf"]["available"] is False
    assert candidate_items["zip"]["available"] is False
    assert "人工确认" in candidate_items["sdf"]["reason"]
    assert client.get(
        f"/api/v1/analyses/{candidate_id}/exports/sdf", headers=auth_headers
    ).status_code == 409

    confirmed = client.post(
        f"/api/v1/analyses/{candidate_id}/review",
        headers=auth_headers,
        json={"action": "confirm"},
    )
    assert confirmed.status_code == 200
    confirmed_center = client.get(f"/api/v1/analyses/{candidate_id}/exports", headers=auth_headers)
    confirmed_items = {item["format"]: item for item in confirmed_center.json()["items"]}
    assert confirmed_items["smi"]["available"] is True
    assert confirmed_items["sdf"]["available"] is True
    assert confirmed_items["zip"]["available"] is True


def test_health_reports_detected_model_queue_and_warmup_state(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, _runs_dir, _auth_headers = harmony_client
    response = client.get("/api/v1/health", headers=API_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["apiVersion"] == "2.5"
    assert payload["modelRuntime"]["state"] == "demo"
    assert "非真实模型" in payload["modelRuntime"]["label"]
    assert payload["modelRuntime"]["warmup"]["state"] == "skip"
    assert payload["modelRuntime"]["weights"]["state"] in {"pass", "skip", "warn"}
    assert set(payload["taskQueue"]) >= {"queued", "running", "active", "failed", "single", "batch"}


def test_feedback_review_api_blocks_pending_manifest_and_exposes_audit(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    client, _runs_dir, auth_headers = harmony_client
    source = Path(api_server.config.SAMPLE_DIR) / "aspirin.png"
    report = MoleculeReportGenerator("manual", tmp_path / "feedback-report").generate(
        smiles="CCO", analysis_id="mobile-feedback-001"
    )
    report["input"].update({
        "type": "image", "filename": "feedback.png", "path": str(source), "image_sha256": "feedback-sha-001",
    })
    corrected = apply_smiles_correction(report, "CCN", tmp_path / "feedback-report")
    save_correction_feedback(
        corrected,
        api_server.config.DATA_DIR,
        correction_type="atom",
        review_status="pending",
        feedback_action="correction_only",
        include_in_training=False,
        source_reference="internal-lab-record",
        source_license="internal",
        notes="awaiting independent review",
    )

    listing = client.get("/api/v1/feedback?status=pending", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["trainingEligibility"]["eligible"] is False

    detail = client.get("/api/v1/feedback/mobile-feedback-001", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["predictedSmiles"] == "CCO"
    assert detail.json()["correctedSmiles"] == "CCN"
    assert detail.json()["sourceLicense"] == "internal"
    assert client.get(detail.json()["imageUrl"]).status_code == 200

    before = client.get("/api/v1/feedback/manifest", headers=auth_headers)
    assert before.status_code == 200
    assert before.json()["exportedCount"] == 0

    approved = client.patch(
        "/api/v1/feedback/mobile-feedback-001/review",
        headers=auth_headers,
        json={"action": "approve", "note": "structure and provenance checked"},
    )
    assert approved.status_code == 200
    assert approved.json()["reviewStatus"] == "verified"
    assert approved.json()["trainingEligibility"]["eligible"] is True
    assert approved.json()["history"][-1]["operation"] == "review_verified"

    after = client.get("/api/v1/feedback/manifest", headers=auth_headers)
    assert after.status_code == 200
    assert after.json()["exportedCount"] == 1
    manifest = client.get(after.json()["item"]["downloadUrl"])
    assert manifest.status_code == 200
    assert b"mobile-feedback-001" in manifest.content


def test_shared_history_creator_and_owner_filters(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, _runs_dir, first_headers = harmony_client
    second = client.post(
        "/api/v1/auth/register",
        headers=API_HEADERS,
        json={
            "username": "chemist",
            "displayName": "Chemist Two",
            "password": "chemist-password-123",
            "role": "化学组",
        },
    )
    assert second.status_code == 201
    second_headers = {
        **API_HEADERS,
        "Authorization": f"Bearer {second.json()['token']}",
    }
    first_result = client.post(
        "/api/v1/analyze-smiles",
        headers=first_headers,
        json={"smiles": "CCO"},
    ).json()
    second_result = client.post(
        "/api/v1/analyze-smiles",
        headers=second_headers,
        json={"smiles": "CC(=O)O"},
    ).json()
    assert second_result["createdBy"]["displayName"] == "Chemist Two"

    all_items = client.get("/api/v1/analyses?scope=all", headers=first_headers).json()["items"]
    assert {first_result["analysisId"], second_result["analysisId"]}.issubset(
        {item["analysisId"] for item in all_items}
    )
    mine = client.get("/api/v1/analyses?scope=mine", headers=first_headers).json()["items"]
    assert {item["analysisId"] for item in mine} == {first_result["analysisId"]}
    owner_id = second.json()["user"]["userId"]
    filtered = client.get(
        f"/api/v1/analyses?scope=all&ownerUserId={owner_id}",
        headers=first_headers,
    ).json()["items"]
    assert {item["analysisId"] for item in filtered} == {second_result["analysisId"]}
    assert filtered[0]["createdBy"]["userId"] == owner_id


def test_sample_and_uploaded_image_jobs_complete(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, runs_dir, auth_headers = harmony_client
    sample_job = client.post("/api/v1/jobs/samples/aspirin", headers=auth_headers)
    assert sample_job.status_code == 202
    assert sample_job.json()["status"] == "queued"
    completed_sample = _wait_for_job(client, sample_job.json()["jobId"], auth_headers)
    assert completed_sample["status"] == "completed"
    assert completed_sample["result"]["inputType"] == "image"
    assert completed_sample["result"]["needsReview"] is True
    assert completed_sample["result"]["createdBy"]["username"] == "researcher"
    assert completed_sample["result"]["sourceImageUrl"]
    assert completed_sample["result"]["imageQuality"]["width"]
    assert completed_sample["result"]["recognitionTrace"]["modelName"]
    source_url = completed_sample["result"]["sourceImageUrl"]
    assert client.get(source_url).status_code == 200
    assert client.get(f"{source_url.split('?')[0]}?token=wrong").status_code == 401
    persisted_job = client.app.state.job_store.load(sample_job.json()["jobId"])
    assert persisted_job["ownerUserId"] == completed_sample["result"]["createdBy"]["userId"]

    sample_path = api_server.config.SAMPLE_DIR / "caffeine.png"
    with sample_path.open("rb") as image_file:
        upload_job = client.post(
            "/api/v1/jobs/images",
            headers=auth_headers,
            files={"file": ("caffeine.png", image_file, "image/png")},
        )
    assert upload_job.status_code == 202
    completed_upload = _wait_for_job(client, upload_job.json()["jobId"], auth_headers)
    assert completed_upload["status"] == "completed"
    assert completed_upload["result"]["analysisId"]

    # A legacy or tampered path never becomes a readable signed media resource.
    sample_analysis_id = completed_sample["result"]["analysisId"]
    report_path = runs_dir / sample_analysis_id / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    outside = runs_dir.parent / "outside.png"
    Image.new("RGB", (10, 10), color="white").save(outside)
    report["input"]["path"] = str(outside)
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    detail = client.get(f"/api/v1/analyses/{sample_analysis_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["sourceImageUrl"] is None
    assert client.get(source_url).status_code == 404


def test_batch_snapshot_can_serve_managed_source_image(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, _runs_dir, auth_headers = harmony_client
    analysis_id = "a" * 32
    job_root = Path(api_server.config.DATA_DIR) / "api_batch_jobs" / "batch_test"
    input_path = job_root / "input" / "0001_test.png"
    report_path = job_root / "outputs" / "batch_ui_result_reports" / f"{analysis_id}.json"
    input_path.parent.mkdir(parents=True)
    report_path.parent.mkdir(parents=True)
    Image.new("RGB", (32, 24), color="white").save(input_path)
    report = {
        "analysis_id": analysis_id,
        "created_at": "2026-07-29T00:00:00+00:00",
        "status": "success",
        "message": "ok",
        "input": {"type": "image", "filename": input_path.name, "path": str(input_path)},
        "ocsr": {"backend": "demo", "predicted_smiles": "CCO"},
        "final": {"smiles": "CCO"},
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    api_server.AnalysisRepository().save_analysis(report, report_path)

    detail = client.get(f"/api/v1/analyses/{analysis_id}", headers=auth_headers)
    assert detail.status_code == 200
    source_url = detail.json()["sourceImageUrl"]
    assert source_url
    assert client.get(source_url).status_code == 200


def test_batch_upload_progress_controls_and_failed_retry(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _runs_dir, auth_headers = harmony_client
    store = client.app.state.batch_job_store
    created_uploads: list[tuple[str, bytes]] = []

    def fake_start_uploads(uploads, backend, runtime_config=None, *, store, owner_user_id=None):
        created_uploads.extend(uploads)
        input_dir = store.job_dir("mobile-batch") / "input"
        output_dir = store.job_dir("mobile-batch") / "outputs"
        input_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        for index, (name, content) in enumerate(uploads):
            (input_dir / f"{index}_{name}").write_bytes(content)
        state = store.create(
            "mobile-batch",
            backend=backend,
            input_dir=input_dir,
            output_dir=output_dir,
            total=len(uploads),
            source="upload",
            owner_user_id=owner_user_id,
        )
        failed_report = {
            "analysis_id": "f" * 32,
            "status": "failed",
            "message": "fixture failure",
            "input": {"type": "image", "filename": "second.png", "path": str(input_dir / "1_second.png")},
            "validation": {"valid": False},
        }
        store.checkpoint_path("mobile-batch").write_text(json.dumps({
            "schema_version": 1,
            "reports_by_path": {str(input_dir / "1_second.png"): failed_report},
        }), encoding="utf-8")
        return store.update(
            "mobile-batch",
            status="running",
            completed=1,
            failed=1,
            current_file="second.png",
            current_index=2,
        )

    monkeypatch.setattr(api_server, "start_batch_job_from_uploads", fake_start_uploads)
    monkeypatch.setattr(api_server, "refresh_batch_job", lambda job_id, store: store.read(job_id))
    monkeypatch.setattr(
        api_server,
        "pause_batch_job",
        lambda job_id, store: store.update(job_id, status="paused", message="paused"),
    )
    monkeypatch.setattr(
        api_server,
        "resume_batch_job",
        lambda job_id, store: store.update(job_id, status="running", message="resumed"),
    )
    monkeypatch.setattr(
        api_server,
        "cancel_batch_job",
        lambda job_id, store, force=False: store.update(job_id, status="cancelling", message="cancelling"),
    )

    image_one = BytesIO()
    image_two = BytesIO()
    Image.new("RGB", (32, 24), "white").save(image_one, format="PNG")
    Image.new("RGB", (24, 32), "white").save(image_two, format="PNG")
    uploaded = client.post(
        "/api/v1/batch-jobs",
        headers=auth_headers,
        files=[
            ("files", ("first.png", image_one.getvalue(), "image/png")),
            ("files", ("second.png", image_two.getvalue(), "image/png")),
        ],
    )
    assert uploaded.status_code == 202
    payload = uploaded.json()
    assert payload["jobId"] == "mobile-batch"
    assert payload["total"] == 2
    assert payload["completed"] == 1
    assert payload["progress"] == 0.5
    assert payload["categoryStats"]["failed"] == 1
    assert payload["results"][0]["filename"] == "second.png"
    assert payload["results"][0]["category"] == "failed"
    assert len(created_uploads) == 2

    assert client.post("/api/v1/batch-jobs/mobile-batch/pause", headers=auth_headers).json()["status"] == "paused"
    assert client.post("/api/v1/batch-jobs/mobile-batch/resume", headers=auth_headers).json()["status"] == "running"
    assert client.post("/api/v1/batch-jobs/mobile-batch/cancel", headers=auth_headers).json()["status"] == "cancelling"

    store.update("mobile-batch", status="failed")
    export_center = client.get("/api/v1/batch-jobs/mobile-batch/exports", headers=auth_headers)
    assert export_center.status_code == 200
    export_items = {item["format"]: item for item in export_center.json()["items"]}
    assert export_items["csv"]["available"] is True
    assert export_items["json"]["available"] is True
    assert export_items["pdf"]["available"] is True
    assert export_items["smi"]["available"] is False
    assert export_items["sdf"]["available"] is False
    assert export_items["zip"]["available"] is False

    def fake_retry(result, backend, mode, runtime_config=None, *, store, parent_job_id=None,
                   analysis_ids=None, owner_user_id=None):
        assert mode == "failed"
        assert len(result["reports"]) == 1
        input_dir = store.job_dir("mobile-retry") / "input"
        output_dir = store.job_dir("mobile-retry") / "outputs"
        input_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        state = store.create(
            "mobile-retry",
            backend=backend,
            input_dir=input_dir,
            output_dir=output_dir,
            total=1,
            source="retry",
            parent_job_id=parent_job_id,
            retry_mode=mode,
            owner_user_id=owner_user_id,
        )
        return store.update("mobile-retry", status="running")

    monkeypatch.setattr(api_server, "start_batch_retry_job", fake_retry)
    retried = client.post("/api/v1/batch-jobs/mobile-batch/retry-failed", headers=auth_headers)
    assert retried.status_code == 202
    assert retried.json()["jobId"] == "mobile-retry"
    assert retried.json()["parentJobId"] == "mobile-batch"
    store.update("mobile-retry", owner_user_id="another-user")
    assert client.get("/api/v1/batch-jobs/mobile-retry", headers=auth_headers).status_code == 404


def test_review_rejects_invalid_without_overwrite_then_confirms(
    harmony_client: tuple[TestClient, Path, dict[str, str]],
) -> None:
    client, _runs_dir, auth_headers = harmony_client
    job = client.post("/api/v1/jobs/samples/aspirin", headers=auth_headers).json()
    completed = _wait_for_job(client, job["jobId"], auth_headers)
    analysis_id = completed["analysisId"]
    original = completed["result"]

    review_page = client.get("/api/v1/analyses?status=review_needed", headers=auth_headers)
    assert any(item["analysisId"] == analysis_id for item in review_page.json()["items"])

    invalid = client.post(
        f"/api/v1/analyses/{analysis_id}/review",
        headers=auth_headers,
        json={"smiles": "C(", "confirm": True},
    )
    assert invalid.status_code == 422
    unchanged = client.get(f"/api/v1/analyses/{analysis_id}", headers=auth_headers).json()
    assert unchanged["smiles"] == original["smiles"]
    assert unchanged["confirmed"] is False

    valid = client.post(
        f"/api/v1/analyses/{analysis_id}/review",
        headers=auth_headers,
        json={"smiles": "CCO", "confirm": True},
    )
    assert valid.status_code == 200
    corrected = valid.json()
    assert corrected["smiles"] == "CCO"
    assert corrected["confirmed"] is True
    assert corrected["needsReview"] is False
    assert corrected["structureImageUrl"]
    assert corrected["review"]["reviewedBy"]["displayName"] == "测试研究员"
    assert corrected["review"]["events"][0]["action"] == "confirm_structure"
    assert client.get(corrected["structureImageUrl"]).status_code == 200

    revoke = client.post(
        f"/api/v1/analyses/{analysis_id}/review",
        headers=auth_headers,
        json={"action": "revoke"},
    )
    assert revoke.status_code == 200
    assert revoke.json()["review"]["status"] == "unconfirmed"
    assert revoke.json()["review"]["events"][0]["action"] == "revoke_confirmation"

    missing_reason = client.post(
        f"/api/v1/analyses/{analysis_id}/review",
        headers=auth_headers,
        json={"action": "unable_to_confirm"},
    )
    assert missing_reason.status_code == 422
    other_without_note = client.post(
        f"/api/v1/analyses/{analysis_id}/review",
        headers=auth_headers,
        json={"action": "unable_to_confirm", "reason": "other"},
    )
    assert other_without_note.status_code == 422

    unable = client.post(
        f"/api/v1/analyses/{analysis_id}/review",
        headers=auth_headers,
        json={
            "action": "unable_to_confirm",
            "reason": "image_unclear",
            "note": "原图局部模糊，需要重新上传。",
        },
    )
    assert unable.status_code == 200
    unable_result = unable.json()
    assert unable_result["confirmed"] is False
    assert unable_result["review"]["status"] == "unable_to_confirm"
    assert unable_result["review"]["reason"] == "image_unclear"
    assert unable_result["review"]["reviewedBy"]["role"] == "算法组"
    assert unable_result["review"]["events"][0]["note"] == "原图局部模糊，需要重新上传。"

    assert client.post(
        f"/api/v1/analyses/{analysis_id}/review",
        headers=auth_headers,
        json={"action": "revoke"},
    ).status_code == 409

    reconfirmed = client.post(
        f"/api/v1/analyses/{analysis_id}/review",
        headers=auth_headers,
        json={"action": "confirm"},
    )
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["review"]["status"] == "confirmed"


def test_completed_batch_status_uses_latest_human_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis_id = "batch-reviewed-analysis"
    snapshot = {
        "analysis_id": analysis_id,
        "status": "success",
        "input": {"type": "image", "filename": "reviewed.png"},
        "ocsr": {"smiles": "CCO", "confidence": 0.91},
        "final": {"smiles": "CCO"},
        "validation": {"valid": True, "canonical_smiles": "CCO"},
        "recognition_decision": {"decision": "review_needed", "manual_review_recommended": True},
        "human_review": {"required": True, "status": "unconfirmed", "confirmed": False},
    }
    latest = {
        **snapshot,
        "human_review": {"required": True, "status": "confirmed", "confirmed": True},
    }

    class FakeRepository:
        def load_report(self, requested_id: str):
            return latest if requested_id == analysis_id else None

    monkeypatch.setattr(api_server, "AnalysisRepository", FakeRepository)
    store = api_server.BatchJobStore(tmp_path / "batch-jobs")
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    store.create("reviewed-batch", backend="fixture", input_dir=input_dir, output_dir=output_dir, total=1)
    store.checkpoint_path("reviewed-batch").write_text(json.dumps({
        "reports_by_path": {str(input_dir / "reviewed.png"): snapshot},
    }), encoding="utf-8")
    state = store.update(
        "reviewed-batch", status="completed", completed=1, review_needed=1,
        summary={"review_needed": 1},
    )

    payload = api_server._batch_job_dto(store, state)

    assert payload["results"][0]["confirmed"] is True
    assert payload["results"][0]["reviewStatus"] == "confirmed"
    assert payload["results"][0]["needsReview"] is False
    assert payload["categoryStats"]["reviewNeeded"] == 0
    assert payload["categoryStats"]["accepted"] == 1


def test_restart_reconciliation_completes_report_or_interrupts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ImmediateExecutor:
        def submit(self, function, *args):
            function(*args)

    class FakeRepository:
        def load_report(self, analysis_id: str):
            if analysis_id == "a" * 32:
                return {"analysis_id": analysis_id, "status": "success"}
            return None

    store = api_server.JobStore(tmp_path / "jobs", ImmediateExecutor())  # type: ignore[arg-type]
    completed = store.create("sample", "a" * 32)
    interrupted = store.create("image", "b" * 32)
    monkeypatch.setattr(api_server, "AnalysisRepository", FakeRepository)
    store.reconcile_after_restart()
    assert store.load(completed["jobId"])["status"] == "completed"
    assert store.load(interrupted["jobId"])["status"] == "failed"
    assert "重新提交" in store.load(interrupted["jobId"])["message"]
