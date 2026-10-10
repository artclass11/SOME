from pathlib import Path

from fastapi.testclient import TestClient

from some.main import app


def test_local_chat_api_and_security_headers(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "todo.txt").write_text("done", encoding="utf-8")
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    client = TestClient(app)

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"

    result = client.post("/api/chat", json={"message": "show my files"})
    assert result.status_code == 200
    assert result.json()["kind"] == "result"
    assert result.json()["result"]["files"][0]["path"] == "todo.txt"
    assert result.json()["receipt_id"]

    receipt = client.get(f"/api/receipts/{result.json()['receipt_id']}")
    assert receipt.status_code == 200
    assert receipt.json()["status"] == "succeeded"
    assert receipt.json()["action"] == "list_files"
    assert receipt.json()["facts"]["count"] == 1
    assert "todo.txt" not in receipt.text

    history = client.get("/api/receipts?limit=10")
    assert history.status_code == 200
    assert history.json()["count"] >= 1
    assert history.json()["items"][0]["id"] == result.json()["receipt_id"]


def test_upload_is_stored_as_data_and_can_be_cleaned(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    client = TestClient(app)

    uploaded = client.post(
        "/api/upload",
        files={"file": ("contacts.csv", b"name,email\nAlice,a@example.com\n", "text/csv")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["receipt_id"]
    upload_receipt = client.get(f"/api/receipts/{uploaded.json()['receipt_id']}")
    assert upload_receipt.status_code == 200
    assert upload_receipt.json()["action"] == "upload_file"
    assert upload_receipt.json()["status"] == "succeeded"
    assert upload_receipt.json()["facts"]["size_bytes"] == len(b"name,email\nAlice,a@example.com\n")
    stored = root / uploaded.json()["file"]
    assert stored.is_file()
    assert stored.read_bytes().startswith(b"name,email")

    cleaned = client.post("/api/chat", json={"message": "clean contacts.csv"})
    assert cleaned.status_code == 200
    assert cleaned.json()["kind"] == "result"
    assert (root / cleaned.json()["result"]["output"]).is_file()


def test_file_moves_need_a_second_confirmation(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "scan.pdf").write_bytes(b"pdf")
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    client = TestClient(app)

    preview = client.post("/api/chat", json={"message": "organize my files"}).json()
    assert preview["kind"] == "plan"
    assert preview["receipt_id"]
    assert (root / "scan.pdf").exists()

    preview_receipt = client.get(f"/api/receipts/{preview['receipt_id']}")
    assert preview_receipt.json()["status"] == "succeeded"
    assert preview_receipt.json()["facts"]["move_count"] == 1

    committed = client.post(f"/api/plans/{preview['plan_id']}/confirm")
    assert committed.status_code == 200
    assert committed.json()["result"]["moved_count"] == 1
    assert committed.json()["result"]["verified_moves"] == 1
    assert committed.json()["receipt_id"]
    committed_receipt = client.get(f"/api/receipts/{committed.json()['receipt_id']}")
    assert committed_receipt.json()["status"] == "succeeded"
    assert committed_receipt.json()["facts"]["verified_moves"] == 1
    assert (root / "organized" / "documents" / "scan.pdf").exists()

    expired = client.post(f"/api/plans/{preview['plan_id']}/confirm")
    assert expired.status_code == 404



def test_cross_origin_state_changes_are_rejected(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "organize my files"},
        headers={"Origin": "https://untrusted.example"},
    )
    assert response.status_code == 403
    assert (root / "keep.txt").exists()


def test_artifacts_can_be_downloaded_only_from_the_workspace(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    client = TestClient(app)

    saved = client.post("/api/chat", json={"message": "create note: private reminder"})
    assert saved.status_code == 200
    relative = saved.json()["result"]["file"]
    downloaded = client.get("/api/artifacts/" + relative)
    assert downloaded.status_code == 200
    assert downloaded.content == b"private reminder\n"
    assert downloaded.headers["cache-control"] == "no-store"

    denied = client.get("/api/artifacts/..%2Foutside.txt")
    assert denied.status_code == 404


def test_ui_uses_external_script_and_restrictive_script_csp():
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert '<script src="/static/app.js" defer></script>' in page.text
    assert "<script>" not in page.text.lower()
    assert "script-src 'self';" in page.headers["content-security-policy"]

    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "fetch(" in script.text
    assert script.headers["x-content-type-options"] == "nosniff"


def test_rejected_cross_origin_responses_keep_security_headers(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "create note: must not be created"},
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self';" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert not (root / "notes").exists()


def test_canceling_a_plan_prevents_later_confirmation(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "scan.pdf").write_bytes(b"pdf")
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    client = TestClient(app)

    preview = client.post("/api/chat", json={"message": "organize my files"}).json()
    assert preview["kind"] == "plan"

    cancelled = client.delete(f"/api/plans/{preview['plan_id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert (root / "scan.pdf").exists()

    confirm = client.post(f"/api/plans/{preview['plan_id']}/confirm")
    assert confirm.status_code == 404
    assert (root / "scan.pdf").exists()


def test_failed_action_creates_failure_receipt(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "broken.csv").write_text("", encoding="utf-8")
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    monkeypatch.setenv("SOME_RECEIPTS_DB", str(tmp_path / "receipts.sqlite3"))
    client = TestClient(app)

    response = client.post("/api/chat", json={"message": "inspect broken.csv"})
    assert response.status_code == 200
    assert response.json()["kind"] == "error"
    receipt_id = response.json()["receipt_id"]
    assert receipt_id

    receipt = client.get(f"/api/receipts/{receipt_id}")
    assert receipt.status_code == 200
    assert receipt.json()["status"] == "failed"
    assert receipt.json()["action"] == "profile_csv"
    assert "broken.csv" not in receipt.text


def test_unknown_receipt_is_not_found_and_receipt_limit_is_bounded():
    client = TestClient(app)
    assert client.get("/api/receipts/not-a-real-receipt").status_code == 404
    assert client.get("/api/receipts?limit=101").status_code == 422


def test_csv_discovery_finds_csv_after_the_500_file_ui_cap(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(501):
        (root / f"a-{index:03}.txt").write_text("not a csv", encoding="utf-8")
    target = root / "z-customers.csv"
    target.write_text("name,amount\nAlice,10\n", encoding="utf-8")
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    monkeypatch.setenv("SOME_RECEIPTS_DB", str(tmp_path / "receipts.sqlite3"))
    monkeypatch.delenv("SOME_OLLAMA_MODEL", raising=False)
    client = TestClient(app)

    # The UI inventory is intentionally capped at 500 displayed files.
    inventory = client.get("/api/files").json()
    assert inventory["truncated"] is True
    assert all(item["path"] != "z-customers.csv" for item in inventory["files"])

    response = client.post("/api/chat", json={"message": "inspect z-customers.csv"})
    assert response.status_code == 200
    assert response.json()["kind"] == "result"
    assert response.json()["result"]["row_count"] == 1


def test_non_csv_rule_action_skips_unneeded_inventory_walk(
    tmp_path: Path, monkeypatch
):
    import some.main as main_module

    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    monkeypatch.setenv("SOME_RECEIPTS_DB", str(tmp_path / "receipts.sqlite3"))
    monkeypatch.delenv("SOME_OLLAMA_MODEL", raising=False)

    def fail_if_called(_root):
        raise AssertionError("CSV inventory should not run for an explicit note command")

    monkeypatch.setattr(main_module, "_files_for_planner", fail_if_called)
    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "create note: keep this local"})

    assert response.status_code == 200
    assert response.json()["kind"] == "result"
    assert (root / response.json()["result"]["file"]).read_text(encoding="utf-8") == "keep this local\n"


def test_frontend_preserves_upload_receipts_and_prevents_duplicate_plan_actions():
    client = TestClient(app)
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "receipt_id: result.receipt_id" in script.text
    assert "if (pending || settled || activeRequests > 0) return;" in script.text
    assert "error.status = response.status" in script.text
    assert "Operation details · review required" in script.text
    assert "Proposed changes · nothing moved yet" in script.text
