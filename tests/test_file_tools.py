"""Local tests for ZIP extraction and the capability catalog; no E2B required."""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from tools.file_tools import FileTools


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="opencoder-filetools-") as temp:
        root = Path(temp)
        tools = FileTools(root)

        # Valid extraction and directory structure preservation.
        source = root / "source"
        source.mkdir()
        (source / "hello.txt").write_text("hello OpenCoder", encoding="utf-8")
        with zipfile.ZipFile(root / "valid.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(source / "hello.txt", "project/docs/hello.txt")
        extracted = tools.zip_extract("valid.zip", "output")
        assert len(extracted) == 1
        assert Path(extracted[0]).read_text(encoding="utf-8") == "hello OpenCoder"

        # Zip Slip must be blocked.
        with zipfile.ZipFile(root / "zip-slip.zip", "w") as archive:
            archive.writestr("../../outside.txt", "no")
        try:
            tools.zip_extract("zip-slip.zip", "blocked")
        except PermissionError:
            pass
        else:
            raise AssertionError("Zip Slip was not blocked")

        # Existing files are not overwritten by default.
        try:
            tools.zip_extract("valid.zip", "output")
        except FileExistsError:
            pass
        else:
            raise AssertionError("Extraction overwrote a file without permission")

        # The maximum entry count is enforced.
        try:
            tools.zip_extract("valid.zip", "limited", max_entries=0)
        except Exception:
            pass
        else:
            raise AssertionError("max_entries was not enforced")

        catalog = tools.tool_capability_catalog()
        assert len(catalog) == 300
        assert len({entry["id"] for entry in catalog}) == 300
        matches = tools.discover_tools("extract ZIP archive", limit=5)
        assert matches["total_capabilities"] == 300
        assert matches["results"]

        # Workspace root and non-empty recursive deletes require explicit confirmation.
        try:
            tools.delete(".")
        except PermissionError:
            pass
        else:
            raise AssertionError("Workspace root deletion was allowed")
        protected = root / "protected"
        protected.mkdir()
        (protected / "keep.txt").write_text("keep", encoding="utf-8")
        try:
            tools.delete("protected", recursive=True)
        except PermissionError:
            pass
        else:
            raise AssertionError("Recursive delete did not require confirmation")
        tools.delete("protected", recursive=True, confirm=True)
        assert not protected.exists()

        # Writes are size-limited and atomic; rejected writes preserve existing content.
        limited = FileTools(root / "limited", max_file_bytes=8, max_read_bytes=8)
        limited.write_file("stable.txt", "safe")
        try:
            limited.write_file("stable.txt", "this is too long")
        except Exception:
            pass
        else:
            raise AssertionError("Oversized write was accepted")
        assert (root / "limited" / "stable.txt").read_text(encoding="utf-8") == "safe"
        try:
            limited.append_file("stable.txt", "too long")
        except Exception:
            pass
        else:
            raise AssertionError("Oversized append was accepted")
        assert (root / "limited" / "stable.txt").read_text(encoding="utf-8") == "safe"

        # Secret files are not readable or included in ordinary listings.
        (root / ".env").write_text("API_KEY=do-not-expose", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("internal", encoding="utf-8")
        (root / ".venv").mkdir()
        (root / ".venv" / "pyvenv.cfg").write_text("internal", encoding="utf-8")
        try:
            tools.read_file(".env")
        except PermissionError:
            pass
        else:
            raise AssertionError("Secret file was readable")
        listed = {entry["name"] for entry in tools.list_files(".")}
        assert ".env" not in listed and ".git" not in listed and ".venv" not in listed

        # ZIP extraction validates all entries before committing any files.
        with zipfile.ZipFile(root / "late-slip.zip", "w") as archive:
            archive.writestr("safe/first.txt", "must not appear")
            archive.writestr("../../escape.txt", "blocked")
        try:
            tools.zip_extract("late-slip.zip", "transactional")
        except PermissionError:
            pass
        else:
            raise AssertionError("Unsafe ZIP entry was accepted")
        assert not (root / "transactional" / "safe" / "first.txt").exists()

        # Duplicate ZIP paths are rejected before extraction.
        with zipfile.ZipFile(root / "duplicate.zip", "w") as archive:
            archive.writestr("same.txt", "first")
            archive.writestr("same.txt", "second")
        try:
            tools.zip_extract("duplicate.zip", "duplicate-output")
        except Exception:
            pass
        else:
            raise AssertionError("Duplicate ZIP entries were accepted")
        assert not (root / "duplicate-output" / "same.txt").exists()

        print("OK: workspace guards, atomic bounded writes, secret filtering, transactional ZIP extraction, and 300-capability catalog")


if __name__ == "__main__":
    run()
