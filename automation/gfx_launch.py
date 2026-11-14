"""Launch adapters for the two genuine backends.

The reference process uses the existing passive OpenGL observer dylib; the
candidate uses the existing Metal boot dylib.  Both are spawned with one shared
host-wide launch lease and immediately registered with the owning session.  The
adapter only prepares env/spawn/readiness; campaign policy lives in
``gfx_dual_run``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

import eu4_benchmark as base  # noqa: E402
import eu4_gfx_startup_trace as startup_trace  # noqa: E402
import eu4_metal_boot as metal_boot  # noqa: E402
from automation import gfx_session, launch_lease  # noqa: E402


class LaunchError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class LaunchConfig:
    executable: Path
    args: list[str]
    executable_sha256: str
    logical_extent: tuple[int, int]
    qualified_sources: Path | None
    shader_cache: Path | None
    ring: int = 8
    deadline_seconds: float = 180.0
    skip_unsupported: bool = False
    lockstep: bool = False
    metadata_only: bool = False
    session_id: str = ""


@dataclass
class Launched:
    role: str
    backend: str
    run_dir: Path
    observe_dir: Path
    process: subprocess.Popen
    identity: gfx_session.ProcessIdentity
    token: str = ""
    env: dict = field(default_factory=dict)
    library: Path | None = None

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "backend": self.backend,
            "run_dir": str(self.run_dir),
            "observe_dir": str(self.observe_dir),
            "pid": self.process.pid,
            "identity": self.identity.as_dict(),
            "token": self.token,
            "library": str(self.library) if self.library else None,
        }


def preflight_user_data(executable: Path) -> dict:
    """Fail early and explicitly when the game cannot write its user data.

    A sandbox denial previously surfaced as an uncaught ``CFileException`` from
    the game and looked like a renderer crash.  The probe is one create/delete
    in the real user-data directory.
    """
    launcher = Path(base.GOG_LAUNCHER)
    try:
        settings = json.loads(launcher.read_text())
        user_data = Path(settings["gameDataPath"]).expanduser()
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise LaunchError("identity", f"cannot resolve the GOG user-data path: {exc}") from exc
    if not user_data.is_dir():
        raise LaunchError("user_data", f"user-data directory is absent: {user_data}")
    probe = user_data / f".gfx-harness-write-probe-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise LaunchError(
            "sandbox_denied",
            f"cannot write {user_data}: {exc}. A real EU4 launch needs normal "
            "user-data write access; this is not a renderer failure.",
        ) from exc
    return {"user_data": str(user_data), "writable": True}


def build_libraries() -> dict:
    try:
        reference = startup_trace.build_library()
    except Exception as exc:  # noqa: BLE001
        raise LaunchError("build", f"OpenGL observer library: {exc}") from exc
    try:
        candidate = metal_boot.build_library("x86_64")
    except Exception as exc:  # noqa: BLE001
        raise LaunchError("build", f"Metal library: {exc}") from exc
    return {"reference": reference, "candidate": candidate}


def _clean_environment() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("EU4_") or key == "DYLD_INSERT_LIBRARIES":
            env.pop(key, None)
    return env


def _common_observation_env(env: dict, run_dir: Path, config: LaunchConfig,
                            role: str) -> Path:
    observe = run_dir / "observe"
    observe.mkdir(parents=True, exist_ok=False)
    env["EU4_GFX_FRAME_OBSERVE_DIR"] = str(observe)
    env["EU4_GFX_FRAME_SESSION"] = config.session_id or f"session-{os.getpid()}"
    env["EU4_GFX_FRAME_RING"] = str(max(0, min(32, config.ring)))
    if config.lockstep:
        env["EU4_GFX_FRAME_LOCKSTEP"] = "1"
        env["EU4_GFX_FRAME_LOCKSTEP_TIMEOUT_MS"] = "8000"
    return observe


def prepare(role: str, config: LaunchConfig, run_dir: Path,
            libraries: dict | None = None,
            backend: str | None = None) -> tuple[dict, Path, Path]:
    """Build the environment and directories for one backend role."""
    if role not in ("reference", "candidate"):
        raise LaunchError("role", role)
    if backend is None:
        backend = "opengl" if role == "reference" else "metal"
    if backend not in ("opengl", "metal"):
        raise LaunchError("backend", backend)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    library = (libraries or build_libraries())[
        "reference" if backend == "opengl" else "candidate"
    ]
    env = _clean_environment()
    env["EU4_GFX_STARTUP_ID"] = config.session_id or f"session-{os.getpid()}"
    env["EU4_GFX_EXPECTED_EXE"] = str(config.executable)
    env["EU4_GFX_EXECUTABLE_SHA256"] = config.executable_sha256
    observe = _common_observation_env(env, run_dir, config, role)
    if backend == "opengl":
        env["DYLD_INSERT_LIBRARIES"] = str(library)
        env["EU4_GFX_STARTUP_DIR"] = str(run_dir)
        env["EU4_GFX_STARTUP_DEADLINE_S"] = str(int(config.deadline_seconds) + 30)
        # The passive observer installs only the entry hooks it is told about.
        # PresentScene is the logical boundary; the draw/clear hooks feed the
        # per-frame composition signature.
        env["EU4_GFX_OPS_TARGETS"] = "PresentScene,_GfxDraw,_GfxClear,_GfxPresent"
        env["EU4_GFX_OPS_MAX_EVENTS"] = "1024"
        env["EU4_GFX_OPS_DURATION_MS"] = str(int(config.deadline_seconds * 1000))
        env["EU4_GFX_OPS_STOP_POLICY"] = "none"
        env["EU4_GFX_OPS_RETENTION"] = "legacy"
        if config.metadata_only:
            env["EU4_GFX_FRAME_METADATA"] = "1"
    else:
        env["DYLD_INSERT_LIBRARIES"] = str(library)
        env["EU4_METAL_BOOT_MODE"] = "metal_init"
        env["EU4_METAL_INIT_DIR"] = str(run_dir)
        env["SDL_VIDEO_EXTERNAL_CONTEXT"] = "1"
        env["EU4_GFX_LIBRARY_SHA256"] = base.sha256(library)
        if config.qualified_sources is None:
            raise LaunchError("config", "Metal requires qualified shader sources")
        if not Path(config.qualified_sources).is_file():
            raise LaunchError("config", f"qualified sources are absent: {config.qualified_sources}")
        env["EU4_GFX_QUALIFIED_SOURCES"] = str(Path(config.qualified_sources))
        if config.shader_cache is None or not Path(config.shader_cache).is_file():
            raise LaunchError(
                "config",
                "Metal requires an existing shader-cache JSON file "
                "(EU4_GFX_SHADER_CACHE is a file, not a directory)",
            )
        env["EU4_GFX_SHADER_CACHE"] = str(Path(config.shader_cache))
        if config.skip_unsupported:
            env["EU4_GFX_DRAW_SKIP_UNSUPPORTED"] = "1"
    return env, run_dir, observe


def spawn(role: str, config: LaunchConfig, run_dir: Path, *, lease: launch_lease.ProcessLease,
          session: gfx_session.Session, token: str = "",
          libraries: dict | None = None,
          backend: str | None = None) -> Launched:
    if backend is None:
        backend = "opengl" if role == "reference" else "metal"
    env, run_dir, observe = prepare(role, config, run_dir, libraries, backend=backend)
    stdout = (run_dir / "game.stdout").open("wb")
    stderr = (run_dir / "game.stderr").open("wb")
    argv = ["./eu4", *config.args]
    try:
        process = subprocess.Popen(
            argv,
            cwd=config.executable.parent,
            env=env,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=lease.child_fds(),
        )
    except BaseException:
        stdout.close()
        stderr.close()
        raise
    finally:
        stdout.close()
        stderr.close()
    identity = session.register(role, process, token=token or None)
    return Launched(
        role=role,
        backend=backend,
        run_dir=run_dir,
        observe_dir=observe,
        process=process,
        identity=identity,
        token=token,
        env=env,
        library=(libraries or {}).get(role),
    )


def status_lines(path: Path) -> dict:
    """Parse the observer's key=value status file; never trusts a partial write."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def reference_installed(run_dir: Path, pid: int) -> bool:
    status = status_lines(Path(run_dir) / "startup.status")
    return status.get("state") == "installed" and status.get("pid") == str(pid)


def candidate_initialized(run_dir: Path, pid: int) -> bool:
    path = Path(run_dir) / "init.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return value.get("success") == 1 and value.get("pid") == pid


def observation_lines(run_dir: Path) -> int:
    path = Path(run_dir) / "observe" / "frame-observe.jsonl"
    try:
        if not path.is_file():
            return 0
        with path.open("rb") as source:
            return sum(1 for line in source if line.strip())
    except OSError:
        return 0
