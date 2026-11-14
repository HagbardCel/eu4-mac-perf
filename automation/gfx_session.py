"""Owned EU4/graphics processes and session isolation.

Two concerns are deliberately separate:

* ``automation.launch_lease`` excludes *other launchers* while this session owns
  the installed executable.
* this module records exactly which PIDs/process groups belong to *this*
  session, verifies their identity before signalling, and never enumerates or
  kills unrelated ``eu4`` processes.

A session has a unique run directory and a host-wide marker file guarded by a
kernel ``flock``.  A marker whose lock is held belongs to a live session; a
marker whose lock can be taken is stale and is reported, never silently
deleted.  Every terminal path writes a structured ``terminal-status.json`` so a
crashed coordinator still leaves an inspectable final state.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import errno
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

SESSION_SCHEMA = "gfx-session/v1"
TERMINAL_SCHEMA = "gfx-terminal-status/v1"
MARKER_SCHEMA = "gfx-session-marker/v1"
ACTIVE = "active"
CLOSED = "closed"
FAILED = "failed"


class SessionError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def session_root() -> Path:
    override = os.environ.get("EU4_GFX_SESSION_ROOT")
    path = Path(override).expanduser() if override else (
        Path(tempfile.gettempdir()) / f"eu4-mac-perf-sessions-{getattr(os, 'getuid', lambda: 0)()}"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


PROC_PIDTBSDINFO = 3
_libproc = None


def _libproc_handle():
    global _libproc
    if _libproc is not None:
        return _libproc
    if sys.platform != "darwin":
        return None
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                 ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pidpath.restype = ctypes.c_int
    _libproc = lib
    return lib


def process_info(pid: int) -> dict | None:
    """Read (ppid, pgid, start time, executable) with libproc.

    The start time makes identity verification resistant to PID reuse.  Returns
    ``None`` when the process is gone or unavailable; never guesses.
    """
    if type(pid) is not int or pid <= 0:
        return None
    lib = _libproc_handle()
    if lib is None:
        return None
    info = _ProcBsdInfo()
    length = lib.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
    if length < ctypes.sizeof(info):
        return None
    buffer = ctypes.create_string_buffer(4096)
    path_length = lib.proc_pidpath(pid, buffer, len(buffer))
    executable = buffer.value.decode(errors="replace") if path_length > 0 else ""
    return {
        "pid": int(info.pbi_pid),
        "ppid": int(info.pbi_ppid),
        "pgid": int(info.pbi_pgid),
        "uid": int(info.pbi_uid),
        "start_seconds": int(info.pbi_start_tvsec),
        "start_microseconds": int(info.pbi_start_tvusec),
        "executable": executable,
        "comm": info.pbi_comm.decode(errors="replace").rstrip("\x00"),
    }


def process_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        return False
    return True


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    pgid: int
    ppid: int
    executable: str
    start_seconds: int
    start_microseconds: int
    role: str = ""

    @classmethod
    def capture(cls, pid: int, *, role: str = "") -> "ProcessIdentity":
        info = process_info(pid)
        if info is None:
            raise SessionError("missing_process", f"pid {pid} is not a live process")
        return cls(
            pid=info["pid"],
            pgid=info["pgid"],
            ppid=info["ppid"],
            executable=info["executable"],
            start_seconds=info["start_seconds"],
            start_microseconds=info["start_microseconds"],
            role=role,
        )

    def current(self) -> dict | None:
        return process_info(self.pid)

    def matches(self) -> bool:
        current = self.current()
        if current is None:
            return False
        return (
            current["start_seconds"] == self.start_seconds
            and current["start_microseconds"] == self.start_microseconds
            and current["executable"] == self.executable
        )

    def as_dict(self) -> dict:
        return {
            "pid": self.pid,
            "pgid": self.pgid,
            "ppid": self.ppid,
            "executable": self.executable,
            "start_seconds": self.start_seconds,
            "start_microseconds": self.start_microseconds,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "ProcessIdentity":
        return cls(
            pid=int(value["pid"]),
            pgid=int(value.get("pgid", value["pid"])),
            ppid=int(value.get("ppid", 0)),
            executable=str(value.get("executable", "")),
            start_seconds=int(value.get("start_seconds", 0)),
            start_microseconds=int(value.get("start_microseconds", 0)),
            role=str(value.get("role", "")),
        )


@dataclass
class Termination:
    role: str
    pid: int
    outcome: str
    signal: str = ""
    returncode: int | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "pid": self.pid,
            "outcome": self.outcome,
            "signal": self.signal,
            "returncode": self.returncode,
            "reason": self.reason,
        }


def terminate_owned(
    identity: ProcessIdentity,
    *,
    process: subprocess.Popen | None = None,
    grace_seconds: float = 8.0,
) -> Termination:
    """Stop one owned process/group, but only after re-verifying identity.

    The process group is signalled only when it is the child's own group
    (``pgid == pid``).  Otherwise only the exact PID is signalled, so a caller
    can never take down an unrelated process that shares a group.
    """
    if not identity.matches():
        return Termination(identity.role, identity.pid, "identity_mismatch",
                           reason="PID was reused or the executable changed; not signalled")
    if process is not None and process.poll() is not None:
        return Termination(identity.role, identity.pid, "already_exited",
                           returncode=process.returncode)
    used_group = identity.pgid == identity.pid
    try:
        if used_group:
            os.killpg(identity.pgid, signal.SIGTERM)
        else:
            os.kill(identity.pid, signal.SIGTERM)
    except OSError as exc:
        return Termination(identity.role, identity.pid, "signal_failed",
                           reason=str(exc))
    if process is not None:
        try:
            process.wait(timeout=max(0.0, grace_seconds))
            return Termination(identity.role, identity.pid, "exited",
                               signal="SIGTERM", returncode=process.returncode)
        except subprocess.TimeoutExpired:
            pass
    else:
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while time.monotonic() < deadline:
            if not identity.matches():
                return Termination(identity.role, identity.pid, "exited", signal="SIGTERM")
            time.sleep(0.1)
    if identity.matches():
        try:
            if used_group:
                os.killpg(identity.pgid, signal.SIGKILL)
            else:
                os.kill(identity.pid, signal.SIGKILL)
        except OSError as exc:
            return Termination(identity.role, identity.pid, "kill_failed", reason=str(exc))
    if process is not None:
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            return Termination(identity.role, identity.pid, "unreaped", signal="SIGKILL")
        return Termination(identity.role, identity.pid, "killed", signal="SIGKILL",
                           returncode=process.returncode)
    return Termination(identity.role, identity.pid, "killed", signal="SIGKILL")


def unique_run_dir(base: Path, label: str) -> Path:
    """Create one never-reused run directory for a session."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)[:48] or "run"
    for _attempt in range(16):
        candidate = base / f"{safe}-{stamp}-{uuid.uuid4().hex[:8]}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise SessionError("collision", f"could not allocate a unique run directory under {base}")


class Session:
    """One owned diagnostic session.  Use :meth:`create`."""

    def __init__(self, run_dir: Path, session_id: str, marker: Path,
                 lock_handle, state: dict):
        self.run_dir = Path(run_dir)
        self.session_id = session_id
        self.marker = Path(marker)
        self._lock = lock_handle
        self._state = state
        self._processes: dict[str, ProcessIdentity] = {}
        self._popens: dict[str, subprocess.Popen] = {}
        self._tokens: list[str] = []
        self._temp_paths: list[Path] = []
        self._closed = False

    # -- construction -----------------------------------------------------
    @classmethod
    def create(
        cls,
        run_root: Path,
        *,
        workflow_id: str,
        profile: str,
        scenario_digest: str | None = None,
        worktree: Path | None = None,
        session_root_path: Path | None = None,
        suffix: str = "",
    ) -> "Session":
        root = Path(session_root_path) if session_root_path is not None else session_root()
        root.mkdir(parents=True, exist_ok=True)
        session_id = (
            dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + uuid.uuid4().hex[:8]
        )
        run_dir = unique_run_dir(Path(run_root), "session" + (f"-{suffix}" if suffix else ""))
        marker = root / f"session-{session_id}.json"
        lock_path = root / f"session-{session_id}.lock"
        handle = lock_path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise SessionError("busy", "could not lock the session marker") from exc
        me = process_info(os.getpid()) or {}
        state = {
            "schema": SESSION_SCHEMA,
            "session_id": session_id,
            "workflow_id": workflow_id,
            "profile": profile,
            "scenario_digest": scenario_digest,
            "worktree": str(Path(worktree).resolve()) if worktree else str(Path.cwd()),
            "run_dir": str(run_dir.resolve()),
            "marker": str(marker.resolve()),
            "coordinator": {
                "pid": os.getpid(),
                "start_seconds": me.get("start_seconds"),
                "start_microseconds": me.get("start_microseconds"),
                "executable": me.get("executable", ""),
            },
            "created_utc": _now(),
            "status": ACTIVE,
            "processes": [],
            "ledger_tokens": [],
            "temporary_paths": [],
        }
        marker_body = {
            "schema": MARKER_SCHEMA,
            "session_id": session_id,
            "run_dir": str(run_dir.resolve()),
            "workflow_id": workflow_id,
            "profile": profile,
            "coordinator_pid": os.getpid(),
            "coordinator_start_seconds": me.get("start_seconds"),
            "created_utc": state["created_utc"],
            "status": ACTIVE,
        }
        _atomic_json(marker, marker_body)
        _atomic_json(run_dir / "session.json", state)
        _atomic_json(run_dir / "terminal-status.json", {
            "schema": TERMINAL_SCHEMA,
            "session_id": session_id,
            "status": ACTIVE,
            "updated_utc": _now(),
            "processes": [],
            "terminations": [],
            "ledger_tokens": [],
            "reason": "",
        })
        return cls(run_dir, session_id, marker, handle, state)

    # -- registry ---------------------------------------------------------
    @staticmethod
    def active_sessions(*, session_root_path: Path | None = None,
                        exclude: str | None = None) -> list[dict]:
        root = Path(session_root_path) if session_root_path is not None else session_root()
        active: list[dict] = []
        if not root.is_dir():
            return active
        for marker in sorted(root.glob("session-*.json")):
            body = _read_json(marker)
            if body is None:
                continue
            if exclude is not None and body.get("session_id") == exclude:
                continue
            lock_path = marker.with_suffix(".lock")
            try:
                handle = lock_path.open("a+")
            except OSError:
                continue
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                body["liveness"] = "active"
                active.append(body)
                handle.close()
                continue
            body["liveness"] = "stale"
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            active.append(body)
        return active

    def other_active_sessions(self) -> list[dict]:
        return [
            row for row in self.active_sessions(exclude=self.session_id)
            if row.get("liveness") == "active"
        ]

    # -- ownership --------------------------------------------------------
    def register(self, role: str, process: subprocess.Popen, *, token: str | None = None,
                 require_child: bool = True) -> ProcessIdentity:
        """Record one process as owned.

        A direct child is verified through ``ppid``.  A process that is not this
        coordinator's child is refused unless the caller explicitly says it
        trusts the handle, so an unrelated EU4 instance can never be adopted.
        """
        if role in self._processes:
            raise SessionError("duplicate_role", f"role {role!r} is already registered")
        identity = ProcessIdentity.capture(process.pid, role=role)
        if require_child and identity.ppid != os.getpid():
            raise SessionError(
                "not_a_child",
                f"pid {identity.pid} is not a child of this coordinator "
                f"(ppid {identity.ppid})",
            )
        self._processes[role] = identity
        self._popens[role] = process
        if token is not None:
            self._tokens.append(token)
        self._state["processes"] = [item.as_dict() for item in self._processes.values()]
        self._state["ledger_tokens"] = list(self._tokens)
        self._write_state()
        return identity

    def owned(self, role: str) -> ProcessIdentity:
        try:
            return self._processes[role]
        except KeyError as exc:
            raise SessionError("unknown_role", role) from exc

    @property
    def processes(self) -> dict[str, ProcessIdentity]:
        return dict(self._processes)

    def owned_temp(self, path: Path) -> Path:
        path = Path(path)
        if not path.resolve().is_relative_to(self.run_dir.resolve()):
            raise SessionError("outside_run_dir", str(path))
        self._temp_paths.append(path)
        self._state["temporary_paths"] = [str(item) for item in self._temp_paths]
        self._write_state()
        return path

    def terminate_all(self, *, grace_seconds: float = 8.0,
                      write: bool = True) -> list[Termination]:
        results: list[Termination] = []
        for role, identity in self._processes.items():
            results.append(
                terminate_owned(identity, process=self._popens.get(role),
                                grace_seconds=grace_seconds)
            )
        if write:
            self._write_terminal(status=CLOSED, reason="terminated",
                                 terminations=results)
        return results

    # -- lifecycle --------------------------------------------------------
    def _write_state(self) -> None:
        _atomic_json(self.run_dir / "session.json", self._state)

    def _write_terminal(self, *, status: str, reason: str,
                        terminations: list[Termination] | None = None,
                        extra: dict | None = None) -> None:
        value = {
            "schema": TERMINAL_SCHEMA,
            "session_id": self.session_id,
            "status": status,
            "updated_utc": _now(),
            "reason": reason,
            "processes": [item.as_dict() for item in self._processes.values()],
            "terminations": [item.as_dict() for item in (terminations or [])],
            "ledger_tokens": list(self._tokens),
            "run_dir": str(self.run_dir.resolve()),
        }
        if extra:
            value.update(extra)
        _atomic_json(self.run_dir / "terminal-status.json", value)

    def close(self, *, status: str = CLOSED, reason: str = "",
              extra: dict | None = None) -> dict:
        if self._closed:
            return _read_json(self.run_dir / "terminal-status.json") or {}
        self._closed = True
        self._write_terminal(status=status, reason=reason, extra=extra)
        self._state["status"] = status
        self._state["closed_utc"] = _now()
        self._write_state()
        marker = _read_json(self.marker) or {"schema": MARKER_SCHEMA,
                                             "session_id": self.session_id}
        marker["status"] = status
        marker["closed_utc"] = _now()
        _atomic_json(self.marker, marker)
        if self._lock is not None:
            try:
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
                self._lock.close()
            except OSError:
                pass
            self._lock = None
        return _read_json(self.run_dir / "terminal-status.json") or {}

    def fail(self, reason: str, *, extra: dict | None = None) -> dict:
        return self.close(status=FAILED, reason=reason, extra=extra)

    def cleanup_temp(self) -> list[str]:
        removed: list[str] = []
        for path in self._temp_paths:
            try:
                resolved = path.resolve()
                if resolved.is_relative_to(self.run_dir.resolve()) and resolved.is_file():
                    resolved.unlink()
                    removed.append(str(resolved))
            except OSError:
                continue
        return removed

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        traces = self.terminate_all(write=False)
        if exc_type is not None:
            self.fail(f"{exc_type.__name__}: {exc}", extra={
                "terminations": [item.as_dict() for item in traces],
            })
        else:
            self.close(status=CLOSED, reason="session completed", extra={
                "terminations": [item.as_dict() for item in traces],
            })

    @classmethod
    def load(cls, run_dir: Path) -> dict:
        state = _read_json(Path(run_dir) / "session.json")
        if state is None:
            raise SessionError("missing", f"no session.json in {run_dir}")
        terminal = _read_json(Path(run_dir) / "terminal-status.json")
        state["terminal"] = terminal
        return state
