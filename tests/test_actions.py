import errno
from pathlib import Path

import pytest

import some.actions as action_module
from some.actions import (
    apply_organization,
    clean_csv,
    create_note,
    find_duplicates,
    list_files,
    preview_organization,
    profile_csv,
)


def test_list_and_duplicate_detection_use_file_contents(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "one.txt").write_text("same", encoding="utf-8")
    (root / "copy.txt").write_text("same", encoding="utf-8")
    (root / "different.txt").write_text("other", encoding="utf-8")

    listing = list_files(root)
    duplicates = find_duplicates(root)

    assert listing["count"] == 3
    assert duplicates["group_count"] == 1
    assert set(duplicates["duplicates"][0]) == {"one.txt", "copy.txt"}


def test_csv_profile_and_clean_preserve_source_and_remove_duplicate_rows(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "customers.csv"
    original = 'Name,Email\n Alice ,alice@example.com\n Alice ,alice@example.com\nBob,=2+2\n'
    source.write_text(original, encoding="utf-8")

    profile = profile_csv("customers.csv", root)
    result = clean_csv("customers.csv", root)
    output = (root / result["output"]).read_text(encoding="utf-8")

    assert profile["row_count"] == 3
    assert profile["duplicate_rows"] == 1
    assert result["rows_before"] == 3
    assert result["rows_after"] == 2
    assert result["duplicate_rows_removed"] == 1
    assert result["formula_like_cells_neutralized"] == 1
    assert source.read_text(encoding="utf-8") == original
    assert "alice@example.com" in output
    assert "'=2+2" in output

    second = clean_csv("customers.csv", root)
    assert second["output"] != result["output"]
    assert (root / result["output"]).read_text(encoding="utf-8") == output


def test_organization_is_previewed_before_it_moves_anything(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "image.png").write_bytes(b"image")
    (root / "table.csv").write_text("a\n1\n", encoding="utf-8")

    plan = preview_organization(root)
    assert plan["move_count"] == 2
    assert (root / "image.png").exists()
    assert (root / "table.csv").exists()

    result = apply_organization(plan["moves"], root)
    assert result["moved_count"] == 2
    assert (root / "organized" / "images" / "image.png").exists()
    assert (root / "organized" / "spreadsheets" / "table.csv").exists()
    assert not (root / "image.png").exists()


def test_create_note_saves_user_supplied_content(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    result = create_note("Call the supplier tomorrow.", root)
    note = root / result["file"]
    assert note.is_file()
    assert note.read_text(encoding="utf-8") == "Call the supplier tomorrow.\n"
    assert result["characters_saved"] == len("Call the supplier tomorrow.")


def test_organization_rolls_back_completed_moves_after_later_failure(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.txt").write_text("two", encoding="utf-8")
    original_link = action_module.os.link
    calls = 0

    def fail_second_link(source, target, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "simulated destination failure")
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(action_module.os, "link", fail_second_link)
    moves = [
        {"source": "one.txt", "target": "organized/documents/one.txt"},
        {"source": "two.txt", "target": "organized/documents/two.txt"},
    ]

    with pytest.raises(OSError, match="simulated destination failure"):
        apply_organization(moves, root)

    assert (root / "one.txt").read_text(encoding="utf-8") == "one"
    assert (root / "two.txt").read_text(encoding="utf-8") == "two"
    assert not (root / "organized" / "documents" / "one.txt").exists()
    assert not (root / "organized" / "documents" / "two.txt").exists()
