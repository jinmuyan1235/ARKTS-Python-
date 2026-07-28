"""Integration coverage for the HarmonyOS local API."""

from __future__ import annotations

from pathlib import Path
from io import BytesIO
import json
import time
from typing import Iterator

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import api_server
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

    assert client.post("/api/v1/auth/logout", headers=login_headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=login_headers).status_code == 401


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
