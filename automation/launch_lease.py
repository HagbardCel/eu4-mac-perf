"""Host-wide advisory process lease for launches of the installed EU4 binary.

The lease is mutual exclusion, not accounting. ``automation.launch_ledger``
remains the only authority for how many launches a workflow may make; this
module only answers "is an EU4 already running for this installation?".

Properties
----------
* One shared lock directory outside any worktree
  (``~/Library/Application Support/eu4-mac-perf/launch-lease``), overridable
  with ``EU4_LAUNCH_LEASE_DIR`` for tests.
* The lock key is the canonical executable identity (resolved path plus device
  and inode), so two worktrees, output directories or DSH sessions contend for
  the same lease.
* Exclusion uses ``flock(2)`` on an open file description. The descriptor is
  inherited by the launched child (``subprocess.Popen(pass_fds=...)``), so the
  kernel keeps the lease held even if this launcher is killed while the game is
  still alive. The lock is released when both the launcher and the game have
  exited. A stale JSON body in the lock file is never treated as the lock.
* A manually started game is detected by process inspection before the lease is
  granted, because it does not cooperate with this module.

The lease does not protect against deliberately malicious code running as the
same operating-system user. It coordinates cooperating launchers, which is the
documented trust model of the project.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import errno
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LEASE_SCHEMA = "eu4-launch-lease/v1"
# A deterministic per-user location outside every worktree. /tmp is used rather
# than ~/Library so a sandboxed DSH shell (workspace-write) can still create the
# lock. Locks are kernel file descriptions, so the directory only has to exist
# long enough for the cooperating launchers; deleting a live lock file would
# break exclusion, which is documented as the remaining limitation.
DEFAULT_LEASE_DIR = Path(tempfile.gettempdir()) / (
    f"eu4-mac-perf-launch-lease-{getattr(os, 'getuid', lambda: 0)()}"
)


class LeaseError(RuntimeError):
    def __init__(self, kind: str, detail: str = "", holder: dict | None = None):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.holder = holder


def lease_dir() -> Path:
    override = os.environ.get("EU4_LAUNCH_LEASE_DIR")
    return Path(override).expanduser() if override else DEFAULT_LEASE_DIR


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _resolved(executable: Path | str) -> Path:
    return Path(executable).expanduser().resolve(strict=False)


def lease_identity(executable: Path | str) -> dict:
    """Canonical, worktree-independent identity of one installed executable."""
    path = _resolved(executable)
    try:
        status = path.stat()
        device, inode = status.st_dev, status.st_ino
    except OSError:
        device, inode = 0, 0
    return {
        "path": str(path),
        "device": int(device),
        "inode": int(inode),
    }


def lease_key(executable: Path | str) -> str:
    identity = lease_identity(executable)
    return f"{identity['device']}-{identity['inode']}-{identity['path']}"


def lease_path(executable: Path | str) -> Path:
    path = lease_dir()
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    # Keep the name readable but bounded; the full identity lives in the body.
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(lease_identity(executable)["path"]).name)
    digest = __import__("hashlib").sha256(lease_key(executable).encode()).hexdigest()[:24]
    return path / f"eu4-{safe or 'game'}-{digest}.lease"


def _read_holder(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _describe_holder(holder: dict | None) -> str:
    if not holder:
        return "another launcher holds the EU4 lease"
    pid = holder.get("pid")
    worktree = holder.get("worktree") or "?"
    return f"EU4 lease is held by pid {pid} ({worktree})"


def _process_executable_path(pid: int) -> str | None:
    if sys.platform != "darwin":
        return None
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    libproc.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    length = libproc.proc_pidpath(pid, buffer, len(buffer))
    return buffer.value.decode(errors="replace") if length > 0 else None


def process_executables() -> list[tuple[int, str]]:
    """Enumerate (pid, executable path) with libproc.

    ``ps`` is unavailable in some sandboxed DSH shells, while libproc is a
    read-only system API. This removes the external process-listing dependency
    from the refusal path.
    """
    if sys.platform != "darwin":
        return []
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
    libproc.proc_listpids.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    libproc.proc_listpids.restype = ctypes.c_int
    libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    libproc.proc_pidpath.restype = ctypes.c_int
    capacity = 8192
    buffer = (ctypes.c_int * capacity)()
    used = libproc.proc_listpids(1, 0, buffer, ctypes.sizeof(buffer))
    if used <= 0:
        return []
    found: list[tuple[int, str]] = []
    for pid in list(buffer)[: used // ctypes.sizeof(ctypes.c_int)]:
        if pid <= 0:
            continue
        path = ctypes.create_string_buffer(4096)
        length = libproc.proc_pidpath(pid, path, len(path))
        if length > 0:
            found.append((pid, path.value.decode(errors="replace")))
    return found


def manual_game_pids(
    executable: Path | str,
    *,
    ps_output: str | None = None,
    resolve_pid=None,
) -> list[int]:
    """PIDs of a manually started game for the same canonical executable.

    ``ps_output`` and ``resolve_pid`` are injectable so the refusal logic can be
    tested without a real game process. With neither, enumeration is libproc
    (not ``ps``).
    """
    wanted = str(_resolved(executable))
    if ps_output is None and resolve_pid is None:
        if sys.platform != "darwin":
            return []
        return [
            pid for pid, path in process_executables()
            if str(Path(path).resolve(strict=False)) == wanted
        ]

    # Explicit test data / resolver: evaluate on every host so portable CI can
    # exercise the matching contract without a real process table or libproc.
    if ps_output is None:
        ps_output = ""
    rows = []
    for line in ps_output.splitlines():
        match = re.match(r"\s*(\d+)\s+(.+)$", line)
        if match is not None:
            rows.append((int(match.group(1)), match.group(2).strip()))
    if resolve_pid is not None:
        resolve = resolve_pid
        candidates = [pid for pid, _comm in rows]
    elif sys.platform == "darwin":
        resolve = _process_executable_path
        candidates = [pid for pid, comm in rows if Path(comm).name == "eu4"]
    else:
        by_pid = {pid: comm for pid, comm in rows}
        resolve = lambda pid: by_pid.get(pid)
        candidates = [pid for pid, _comm in rows]
    found = []
    for pid in candidates:
        try:
            resolved = resolve(pid)
        except Exception:  # noqa: BLE001 - a vanished process is simply not a match
            resolved = None
        if resolved is not None and str(Path(resolved).resolve(strict=False)) == wanted:
            found.append(pid)
    return found


class ProcessLease:
    """One acquired advisory lease. Use :func:`acquire` rather than this class."""

    def __init__(self, executable: Path | str, fd: int, path: Path, identity: dict):
        self.executable = _resolved(executable)
        self.identity = identity
        self.path = path
        self._fd: int | None = fd
        self._closed = False

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise LeaseError("released", "lease descriptor is closed")
        return self._fd

    def child_fds(self) -> tuple[int, ...]:
        """Descriptor list for ``subprocess.Popen(pass_fds=...)``."""
        return (self.fd,)

    def holder(self) -> dict | None:
        return _read_holder(self.path)

    def check_manual_game(self) -> None:
        pids = manual_game_pids(self.executable)
        if pids:
            raise LeaseError(
                "manual",
                f"an EU4 process is already running (pid {', '.join(map(str, pids))})",
            )

    def release(self) -> None:
        if self._closed:
            return
        self._closed = True
        fd, self._fd = self._fd, None
        if fd is None:
            return
        # Do NOT flock(LOCK_UN) here. The child inherited the same open file
        # description; an explicit unlock would release the lock for the game
        # as well. Closing this duplicate is what keeps the lease alive until
        # the last (game) descriptor is closed by process exit.
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> "ProcessLease":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    def __del__(self):
        try:
            self.release()
        except Exception:  # noqa: BLE001 - interpreter shutdown
            pass


def inspect(executable: Path | str) -> dict:
    """Report the current lease state without acquiring it.

    Returns ``{"state": "free"|"busy"|"manual", "holder": {...},
    "manual_game_pids": [...]}``.  This is a read-only diagnostic for agents that
    should not attempt a pointless launch; the lease itself remains the
    authority and is still acquired immediately before spawn.
    """
    path = lease_path(executable)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    state = "free"
    holder = None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise LeaseError("unavailable", str(exc)) from exc
            state = "busy"
            holder = _read_holder(path)
        pids = manual_game_pids(executable)
        if pids:
            state = "manual" if state == "free" else state
            holder = holder or {"manual_game_pids": pids}
        return {"state": state, "holder": holder, "manual_game_pids": pids}
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def acquire(
    executable: Path | str,
    *,
    wait: float = 0.0,
    output: Path | str | None = None,
    check_manual: bool = True,
) -> ProcessLease:
    """Acquire the host-wide lease for ``executable``.

    ``wait`` of zero refuses immediately when another cooperative launcher or a
    manually started game holds the installation. No JSON status is polled.
    """
    path = lease_path(executable)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    deadline = time.monotonic() + max(0.0, wait)
    refused = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                os.close(fd)
                raise LeaseError("unavailable", str(exc)) from exc
            refused = True
            if time.monotonic() >= deadline:
                holder = _read_holder(path)
                os.close(fd)
                raise LeaseError("busy", _describe_holder(holder), holder)
            time.sleep(0.1)
    identity = lease_identity(executable)
    body = {
        "schema": LEASE_SCHEMA,
        "pid": os.getpid(),
        "worktree": str(Path.cwd()),
        "output": str(output) if output is not None else None,
        "executable": identity["path"],
        "executable_device": identity["device"],
        "executable_inode": identity["inode"],
        "acquired_at": _now(),
        "waited": refused,
    }
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, (json.dumps(body, indent=2) + "\n").encode())
        os.fsync(fd)
    except OSError:
        # Diagnostics are best effort; the kernel lock is the authority.
        pass
    lease = ProcessLease(executable, fd, path, identity)
    if check_manual:
        try:
            lease.check_manual_game()
        except Exception:
            lease.release()
            raise
    return lease


def launched(
    executable: Path | str,
    argv: list[str],
    *,
    output: Path | str | None = None,
    popen: dict | None = None,
    pre_spawn=None,
):
    """Acquire, spawn one child that inherits the lease, and yield the Popen.

    ``pre_spawn`` runs after the lease is held and immediately before process
    creation. That ordering is where the final idle check belongs. The caller
    owns process termination and reaping. The lease is released when the
    ``with`` block exits; the child's inherited descriptor keeps the kernel
    lock until the game exits even if the launcher dies first.
    """
    lease = acquire(executable, output=output)
    if pre_spawn is not None:
        try:
            pre_spawn()
        except Exception:
            lease.release()
            raise
    options = dict(popen or {})
    existing = tuple(options.pop("pass_fds", ()))
    options["pass_fds"] = existing + lease.child_fds()
    try:
        process = subprocess.Popen(argv, **options)
    except Exception:
        lease.release()
        raise
    return _Launched(lease, process)


class _Launched:
    def __init__(self, lease: ProcessLease, process: subprocess.Popen):
        self.lease = lease
        self.process = process

    def __enter__(self) -> subprocess.Popen:
        return self.process

    def __exit__(self, *_exc) -> None:
        self.lease.release()
