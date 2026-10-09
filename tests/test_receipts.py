from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from some.main import app
from some.receipts import (
    acknowledge_recovery,
    finish_receipt,
    get_receipt,
    list_receipts,
    receipt_facts,
    receipt_summary,
    recover_interrupted_receipts,
    start_receipt,
)


def test_receipt_is_durable_and_omits_private_content(tmp_path: Path, monkeypatch):
    database = tmp_path / "state" / "receipts.sqlite3"
    monkeypatch.setenv("SOME_RECEIPTS_DB", str(database))
    secret_note = "private customer callback: 9876543210"
    result = {"file": "notes/secret-name.txt", "characters_saved": len(secret_note)}

    receipt_id = start_receipt("create_note")
    saved = finish_receipt(
        receipt_id,
        "succeeded",
        receipt_summary("create_note", result),
        receipt_facts("create_note", result),
    )

    assert database.is_file()
    assert saved["status"] == "succeeded"
    assert saved["facts"] == {"characters_saved": len(secret_note)}
    assert "secret-name.txt" not in str(saved)
    assert secret_note not in database.read_text(encoding="utf-8", errors="ignore")
    assert get_receipt(receipt_id) == saved
    assert list_receipts(limit=10)[0] == saved


def test_process_restart_marks_running_actions_interrupted_and_acknowledgeable(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("SOME_RECEIPTS_DB", str(tmp_path / "receipts.sqlite3"))
    receipt_id = start_receipt("clean_csv")

    assert recover_interrupted_receipts() == 1
    receipt = get_receipt(receipt_id)
    assert receipt is not None
    assert receipt["status"] == "interrupted"
    assert "inspect the workspace" in receipt["summary"]

    acknowledged = acknowledge_recovery(receipt_id)
    assert acknowledged is not None
    assert acknowledged["recovery_acknowledged_at"] is not None
    # Acknowledgement is idempotent and doesn't rewrite the interruption status.
    assert acknowledge_recovery(receipt_id) == acknowledged
    assert get_receipt(receipt_id)["status"] == "interrupted"


def test_final_receipt_cannot_be_rewritten(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SOME_RECEIPTS_DB", str(tmp_path / "receipts.sqlite3"))
    receipt_id = start_receipt("list_files")
    finish_receipt(receipt_id, "succeeded", "Listed 2 file(s).", {"count": 2})

    with pytest.raises(ValueError, match="no longer running"):
        finish_receipt(receipt_id, "failed", "Overwritten status.")


def test_receipt_limits_are_validated(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SOME_RECEIPTS_DB", str(tmp_path / "receipts.sqlite3"))
    with pytest.raises(ValueError, match="between 1 and 100"):
        list_receipts(limit=101)
    with pytest.raises(ValueError, match="out of range"):
        list_receipts(offset=-1)


def test_app_startup_marks_previously_running_receipts_interrupted(
    tmp_path: Path, monkeypatch
):
    database = tmp_path / "receipts.sqlite3"
    monkeypatch.setenv("SOME_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("SOME_RECEIPTS_DB", str(database))
    (tmp_path / "workspace").mkdir()

    receipt_id = start_receipt("clean_csv")
    with TestClient(app) as client:
        response = client.get(f"/api/receipts/{receipt_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "interrupted"
    assert "inspect the workspace" in response.json()["summary"]
