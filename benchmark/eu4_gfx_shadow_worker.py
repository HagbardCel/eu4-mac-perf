import json
import os
import select
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "benchmark" / ".build" / "eu4_gfx_shadow_worker"


def build():
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "clang",
        "-arch",
        "x86_64",
        "-O2",
        "-Wall",
        "-Wextra",
        "-fobjc-arc",
        "-lc++",
        "-I",
        str(ROOT / "benchmark"),
        "-framework",
        "Foundation",
        "-framework",
        "Metal",
        "-framework",
        "QuartzCore",
        "-o",
        str(BINARY),
        str(ROOT / "benchmark" / "eu4_gfx_shadow_worker.mm"),
        str(ROOT / "benchmark" / "eu4_gfx_init.mm"),
        str(ROOT / "benchmark" / "eu4_gfx_buffers.mm"),
        str(ROOT / "benchmark" / "eu4_metal_events.c"),
    ]
    built = subprocess.run(command, capture_output=True, text=True, check=False)
    if built.returncode:
        raise RuntimeError(built.stderr.strip() or "shadow worker failed to build")
    return BINARY


class Worker:
    def __init__(self, binary=None, env=None):
        self.err_file = tempfile.TemporaryFile(mode="w+b")
        child = os.environ.copy()
        child["MTL_DEBUG_LAYER"] = "1"
        child["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
        if env:
            child.update(env)
        self.proc = subprocess.Popen(
            [str(binary or BINARY)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.err_file,
            env=child,
            bufsize=0,
        )
        self._buf = bytearray()
        self.next_seq = 1

    def stderr_text(self):
        self.err_file.flush()
        self.err_file.seek(0)
        return self.err_file.read().decode()

    def read(self, timeout=30):
        deadline = time.monotonic() + timeout
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                raw = bytes(self._buf[:nl])
                del self._buf[: nl + 1]
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
                return json.loads(raw.decode())
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(self.stderr_text() or "shadow worker timed out")
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                raise TimeoutError(self.stderr_text() or "shadow worker timed out")
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if chunk == b"":
                if self._buf:
                    raise RuntimeError("incomplete response")
                return None
            self._buf.extend(chunk)

    def line(self, text):
        payload = text if text.endswith("\n") else text + "\n"
        self.proc.stdin.write(payload.encode())
        self.proc.stdin.flush()
        resp = self.read()
        if isinstance(resp, dict) and isinstance(resp.get("next"), int):
            self.next_seq = resp["next"]
        return resp

    def call(self, op, args=None, seq=None):
        if seq is None:
            seq = self.next_seq
        body = {"schema": "metal-shadow/v0", "seq": seq, "op": op, "args": args or {}}
        return self.line(json.dumps(body))

    def close(self):
        if self.proc.poll() is None and self.proc.stdin is not None and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            code = self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait(timeout=10)
        for pipe in (self.proc.stdin, self.proc.stdout):
            if pipe is not None and not pipe.closed:
                pipe.close()
        if not self.err_file.closed:
            self.err_file.close()
        return code
