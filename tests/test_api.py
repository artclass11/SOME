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


def test_upload_is_stored_as_data_and_can_be_cleaned(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("SOME_WORKSPACE", str(root))
    client = TestClient(app)

    uploaded = client.post(
        "/api/upload",
        files={"file": ("contacts.csv", b"name,email\\nAlice,a@example.com\\n", "text/csv")},
    )
    assert uploaded.status_code == 200
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
    assert (root / "scan.pdf").exists()

    committed = client.post(f"/api/plans/{preview['plan_id']}/confirm")
    assert committed.status_code == 200
    assert committed.json()["result"]["moved_count"] == 1
    assert (root / "organized" / "documents" / "scan.pdf").exists()

    expired = client.post(f"/api/plans/{preview['plan_id']}/confirm")
    assert expired.status_code == 404
