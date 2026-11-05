import json
import os
import select
import subprocess
import tempfile
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
    def __init__(self, binary=None):
        self.err_file = tempfile.TemporaryFile(mode="w+")
        env = os.environ.copy()
        env["MTL_DEBUG_LAYER"] = "1"
        env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
        self.proc = subprocess.Popen(
            [str(binary or BINARY)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.err_file,
            env=env,
            text=True,
        )
        self.next_seq = 1

    def stderr_text(self):
        self.err_file.flush()
        self.err_file.seek(0)
        return self.err_file.read()

    def read(self, timeout=30):
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError(self.stderr_text() or "shadow worker timed out")
        line = self.proc.stdout.readline()
        if line == "":
            return None
        return json.loads(line)

    def line(self, text):
        self.proc.stdin.write(text if text.endswith("\n") else text + "\n")
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
