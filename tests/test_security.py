from pathlib import Path

import pytest

from some.security import safe_filename, safe_path


def test_safe_path_rejects_absolute_and_traversal(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_path("../outside.txt", root)
    with pytest.raises(ValueError):
        safe_path(str(tmp_path / "outside.txt"), root)
    with pytest.raises(ValueError):
        safe_path("folder\\outside.txt", root)


def test_safe_path_rejects_symlink(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("private", encoding="utf-8")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not available on this platform.")
    with pytest.raises(ValueError):
        safe_path("linked/secret.txt", root, must_exist=True)


def test_safe_filename_removes_client_path_components():
    assert safe_filename("../../customers.csv") == "customers.csv"
    assert safe_filename(r"C:\temp\sales report.csv") == "sales report.csv"
    with pytest.raises(ValueError):
        safe_filename("..")
