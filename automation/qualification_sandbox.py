"""Native worker boundary for independent offline qualification."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import time
from threading import current_thread, main_thread

from automation import campaign_guard


def run(command, *, writable: Path, readable=(), tools=(), timeout: float = 60,
        input: str | None = None, graphics: str | None = None,
        original_executable: tuple[str, str] | None = None) -> subprocess.CompletedProcess:
    """Supervise one Seatbelt job. Compiler subprocesses inherit the fence."""
    command = [str(arg) for arg in command]
    program = Path(command[0])
    if not program.is_absolute():
        raise ValueError("qualification worker executable must be absolute")
    writable = writable.resolve(strict=True)
    paths = (Path(path).resolve(strict=True) for path in (*readable, writable))
    executables = {str(path) for tool in (program, *tools)
                   for path in (Path(tool), Path(tool).resolve(strict=True))}
    profile = [
        "(version 3)",
        "(deny default)",
        '(import "dyld-support.sb")',
        "(allow syscall* process-info* sysctl-read file-read-metadata file-test-existence)",
        '(allow system-mac-syscall (mac-policy-name "AMFI"))',
        '(allow process-exec-interpreter (literal "/usr/lib/dyld"))',
        '(allow file-read* file-map-executable (subpath "/System/Library") '
        '(subpath "/usr/lib") (subpath "/usr/share") (literal "/dev/null"))',
        '(allow file-write-data (literal "/dev/null"))',
    ]
    for path in paths:
        rule = "subpath" if path.is_dir() else "literal"
        profile.append(f"(allow file-read* file-map-executable ({rule} {json.dumps(str(path))}))")
    for tool in sorted(executables):
        profile.append(f"(allow process-exec* file-read* file-map-executable (literal {json.dumps(tool)}))")
    if tools:
        profile.append("(allow process-fork)")
    if graphics is not None:
        profile.extend([
            '(import "system.sb")',
            "(system-graphics)",
            "(allow iokit-get-properties)",
            '(allow iokit-open-service (iokit-registry-entry-class-prefix "AGXAcceleratorG"))',
            '(allow iokit-open-user-client (iokit-user-client-class "AGXDevice" '
            '"AGXGLContext" "AGXDeviceUserClient" "AGXCommandQueue" "AGXSharedUserClient" '
            '"IOGPUDeviceUserClient" "IOSurfaceRootUserClient" "AGPMClient"))',
            '(allow mach-lookup (global-name "com.apple.windowserver.active" '
            '"com.apple.MTLCompilerService"))',
        ])
    if graphics == "metal":
        cache = Path(os.confstr(65538)).resolve(strict=True)  # _CS_DARWIN_USER_CACHE_DIR
        compiler_cache = " ".join(
            f"(subpath {json.dumps(str(cache / name))})"
            for name in ("com.apple.metalfe", "com.apple.gpuarchiver"))
        # Delegate only Apple's cache directories to the system compiler.
        # The worker has no cache file access or extension-consumption rule.
        profile.extend([
            '(allow system-mac-syscall (require-all (mac-policy-name "Sandbox") '
            '(mac-syscall-number 5)))',
            '(allow file-issue-extension (require-all '
            '(extension-class "com.apple.app-sandbox.read" "com.apple.app-sandbox.read-write") '
            f'(require-any (subpath {json.dumps(str(writable))}) {compiler_cache})))',
        ])
    profile.append(f"(allow file-write* (subpath {json.dumps(str(writable))}))")
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(writable),
           "TMPDIR": str(writable) + "/", "EU4_TEST_PROFILE": "offline",
           "EU4_OFFLINE_ONLY": "1", "EU4_QUALIFICATION_WORKER": "1"}
    if original_executable is not None:
        env["EU4_GFX_EXPECTED_EXE"], env["EU4_GFX_EXECUTABLE_SHA256"] = original_executable
    campaign_guard.require_active_from_env()
    expires = time.monotonic() + timeout
    def interrupted(_number, _frame):
        raise InterruptedError("qualification worker was cancelled")

    with subprocess.Popen(
        ["/usr/bin/sandbox-exec", "-p", "\n".join(profile), *command],
        cwd=writable, env=env, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, text=True,
    ) as child:
        previous_handler = None
        try:
            if current_thread() is main_thread():
                previous_handler = signal.signal(signal.SIGTERM, interrupted)
            while True:
                campaign_guard.require_active_from_env()
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = child.communicate(input=input, timeout=min(0.5, remaining))
                except subprocess.TimeoutExpired:
                    input = None
                else:
                    campaign_guard.require_active_from_env()
                    return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)
