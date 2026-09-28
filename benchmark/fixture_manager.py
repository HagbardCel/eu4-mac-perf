"""Transactional continuation-file and disposable-save management for EU IV."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

import eu4_benchmark as base


def atomic_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class FixtureManager:
    def __init__(self, output_root: Path, user_data: Path, fixture: Path, digest: str):
        self.output_root = output_root
        self.user_data = user_data
        self.fixture = fixture
        self.digest = digest
        self.continue_path = user_data / "continue_game.json"
        self.journal = output_root / ".autonomous-recovery.json"
        self.lock_path = output_root / ".autonomous.lock"
        self.lock_fd: int | None = None
        self.working_save = user_data / "save games" / f"EU4Benchmark_{digest[:16]}.eu4"

    def __enter__(self):
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.lock_fd)
            self.lock_fd = None
            raise base.BenchmarkError("Another autonomous EU IV runner holds the fixture lock") from exc
        return self

    def __exit__(self, *_exc):
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd = None

    def recover(self) -> bool:
        """Call only after verifying that no EU IV process is running."""
        if not self.journal.is_file():
            return False
        entry = json.loads(self.journal.read_text())
        if entry.get("continue_path") != str(self.continue_path):
            raise base.BenchmarkError("Recovery journal points to an unexpected continuation file")
        if entry["existed"]:
            original = base64.b64decode(entry["original_b64"], validate=True)
            atomic_bytes(self.continue_path, original, entry["mode"])
        else:
            self.continue_path.unlink(missing_ok=True)
        self.journal.unlink()
        return True

    def install(self) -> Path:
        if self.journal.exists():
            raise base.BenchmarkError("Recover the previous continuation file before installing a new fixture")
        if base.sha256(self.fixture) != self.digest:
            raise base.BenchmarkError("Fixture checksum changed")
        if self.continue_path.is_symlink() or self.working_save.is_symlink():
            raise base.BenchmarkError("Refusing a symlink in the GOG continuation path")
        self.working_save.parent.mkdir(parents=True, exist_ok=True)
        if self.working_save.exists():
            if base.sha256(self.working_save) != self.digest:
                raise base.BenchmarkError("Existing working save differs from the fixture")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".EU4Benchmark.", dir=self.working_save.parent)
            try:
                with os.fdopen(fd, "wb") as output, self.fixture.open("rb") as source:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                if base.sha256(Path(temporary)) != self.digest:
                    raise base.BenchmarkError("Working save copy failed checksum verification")
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.working_save)
            finally:
                Path(temporary).unlink(missing_ok=True)
        existed = self.continue_path.exists()
        original = self.continue_path.read_bytes() if existed else b""
        mode = stat.S_IMODE(self.continue_path.stat().st_mode) if existed else 0o600
        entry = {"continue_path": str(self.continue_path), "existed": existed,
                 "original_b64": base64.b64encode(original).decode(), "mode": mode}
        atomic_bytes(self.journal, (json.dumps(entry, sort_keys=True)+"\n").encode())
        content = {"title": "Venice 1444.11.11", "desc": "EU IV benchmark fixture",
                   "date": "1444.11.11", "filename": f"save games/{self.working_save.name}"}
        try:
            atomic_bytes(self.continue_path, (json.dumps(content, indent=2)+"\n").encode())
        except BaseException:
            self.recover()
            raise
        return self.working_save

    def finish(self, run_dir: Path) -> bool:
        """Preserve a changed working save for diagnosis; restore user state."""
        changed = self.working_save.exists() and base.sha256(self.working_save) != self.digest
        try:
            if changed:
                shutil.copy2(self.working_save, run_dir / "working_save_changed.eu4")
            self.recover()
        finally:
            if self.working_save.exists():
                self.working_save.unlink()
        return changed
