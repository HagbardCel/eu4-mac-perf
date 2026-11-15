#!/bin/sh
# Fail-closed checks on the effective eu4-web profile, before any goal boots.
#
# Verifies the pinned runtime, that every native goal service is mounted and
# enabled somewhere in the tree, that our host-plane skill root won, that the
# plugin resolves to a cordis plugin, and that the shared skill exists.
# Prints one JSON object; exits non-zero on the first failing class.

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)
PROFILE=eu4-web
RUNTIME=$INFRA/dsh/runtime.json

# The dedicated home is deliberate. An inherited DSH_HOME (the interactive
# session's ~/.dsh) must never be reused: this profile owns its own sessions,
# plugins and settings, and must not touch the user's normal installation.
: "${EU4_DSH_HOME:=$HOME/.local/share/eu4-dsh}"
DSH_HOME=$EU4_DSH_HOME
export DSH_HOME
export EU4_DSH_SKILL_ROOT=${EU4_DSH_SKILL_ROOT:-$INFRA/.opencode/skills}
PROFILE_DIR=$DSH_HOME/profiles/$PROFILE
PATCH=$PROFILE_DIR/cordis.patch.yml

# Resolve the interpreter the tools plugin will spawn before dumping the
# profile, so the composed tree and this check use the same absolute path. The
# M7 canary failed because the native tool used a different interpreter than
# the documented CLI; a mismatch must fail here, never at tool-call time.
PYTHON_REQUEST=${EU4_DSH_PYTHON:-}
if [ -z "$PYTHON_REQUEST" ]; then
  if [ -x "$INFRA/.venv/bin/python3" ]; then
    PYTHON_REQUEST=$INFRA/.venv/bin/python3
  elif command -v python3.14 >/dev/null 2>&1; then
    PYTHON_REQUEST=$(command -v python3.14)
  else
    PYTHON_REQUEST=$(command -v python3 || true)
  fi
fi
[ -n "$PYTHON_REQUEST" ] || {
  echo "$(basename "$0"): no Python 3 interpreter is available on PATH" >&2
  exit 1
}
EU4_DSH_PYTHON=$(
  cd "$INFRA" && "$PYTHON_REQUEST" -m automation.python_runtime \
    resolve --requested "$PYTHON_REQUEST" --print-path
) || {
  printf '{"ok":false,"stage":"python","detail":"no Python 3 interpreter with NumPy; install NumPy or set EU4_DSH_PYTHON to an absolute interpreter that has it"}\n'
  (
    cd "$INFRA" && "$PYTHON_REQUEST" -m automation.python_runtime \
      resolve --requested "$PYTHON_REQUEST" --json >&2
  ) || true
  exit 1
}
export EU4_DSH_PYTHON

DSH_ENTRY=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$(command -v dsh)")
DSH_PACKAGE=$(CDPATH= cd -- "$(dirname -- "$(dirname -- "$DSH_ENTRY")")" && pwd)
DUMP=$(mktemp -t eu4-dsh-dump.XXXXXX)
trap 'rm -f "$DUMP"' EXIT

fail() {
  printf '{"ok":false,"stage":"%s","detail":"%s"}\n' "$1" "$2"
  exit 1
}

[ -d "$PROFILE_DIR" ] || fail provision "profile missing: run dsh/scripts/provision.sh --init"
[ -f "$PATCH" ] || fail provision "profile overlay missing: $PATCH"

dsh --profile "$PROFILE" --dump-config >"$DUMP" 2>&1 ||
  fail dump-config "dsh --dump-config failed"

"$EU4_DSH_PYTHON" - "$DUMP" "$RUNTIME" "$PATCH" "$EU4_DSH_SKILL_ROOT" "$INFRA" "$DSH_PACKAGE" "$PROFILE_DIR" "$EU4_DSH_PYTHON" <<'PY' || exit 1
import hashlib, json, os, re, sys

(dump_path, runtime_path, patch_path, skill_root, infra, dsh_package,
 profile_dir, python_path) = sys.argv[1:9]
text = open(dump_path, encoding="utf-8").read()
runtime = json.load(open(runtime_path, encoding="utf-8"))
sys.path.insert(0, infra)
from automation import dsh_profile


def bail(stage, detail):
    print(json.dumps({"ok": False, "stage": stage, "detail": detail}))
    sys.exit(1)


# A patch that addresses a non-existent id prints "patch: entry X not found"
# and silently drops the intent, so treat the warning itself as a failure.
warnings = [l for l in text.splitlines() if "patch: entry" in l and "not found" in l]
if warnings:
    bail("patch", warnings[0].strip())

# Split the composed tree into entries: one per "- id: <name>" line, with the
# entry's own indentation and its body up to the next entry.
entries = []
lines = text.splitlines()
starts = [(i, len(l) - len(l.lstrip()), l.strip()) for i, l in enumerate(lines)
          if l.strip().startswith("- id: ")]
for n, (i, indent, head) in enumerate(starts):
    end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
    entries.append({
        "id": head.split("- id: ", 1)[1].strip(),
        "indent": indent,
        "body": "\n".join(lines[i:end]),
    })


def enabled(entry):
    return not re.search(r"^\s*disabled:\s*true\s*$", entry["body"], re.M)


# Every required plugin is named once, in runtime.json; preflight requires the
# effective profile to mount and enable all of them. This keeps runtime.json,
# provision.sh and the tracked overlay from drifting apart.
required = list(runtime["required_plugins"])
for name in required:
    matches = [e for e in entries if e["id"] == name]
    if not matches:
        bail(name, "required plugin row '%s' absent from the effective profile" % name)
    if not any(enabled(e) for e in matches):
        bail(name, "plugin row '%s' is present but disabled everywhere" % name)

# Package identity pins: the DSH release itself, the five native bundles the
# profile depends on, and the two local plugins linked from this checkout.
def pin(path, expected, label):
    if not os.path.isfile(path):
        bail("plugin-pin", "%s package.json missing: %s" % (label, path))
    with open(path, "rb") as source:
        digest = hashlib.sha256(source.read()).hexdigest()
    if not expected:
        bail("plugin-pin", "%s has no package_json_sha256" % label)
    if digest != expected:
        bail("plugin-pin", "%s package.json sha256 %s != pinned %s"
             % (label, digest, expected))


pin(os.path.join(dsh_package, "package.json"),
    runtime["dsh"].get("package_json_sha256"), "dsh")

for plugin_id, spec in runtime["required_plugins"].items():
    if spec.get("package_json"):
        # Local plugin: validate the package the profile actually resolves,
        # and require it to be the canonical package from this checkout.
        problems = dsh_profile.verify_local_plugin(
            plugin_id, spec, profile_dir, infra)
        if problems:
            bail("plugin-pin", "; ".join(problems))
    else:
        # Shipped bundle: resolve from the DSH install the launcher will boot.
        path = os.path.join(dsh_package, "node_modules", spec["name"], "package.json")
        pin(path, spec.get("package_json_sha256"), plugin_id)

# The host-plane skill root must be ours and must win over the bundle disable.
host = [e for e in entries if e["id"] == "skill-filesystem" and e["indent"] == 0]
if not host:
    bail("skill-filesystem", "no host-plane skill-filesystem row")
if not enabled(host[0]):
    bail("skill-filesystem", "host-plane skill-filesystem row is disabled")
if "customSkillDirs" not in host[0]["body"]:
    bail("skill-filesystem", "host-plane row has no customSkillDirs")
if "EU4_DSH_SKILL_ROOT" not in host[0]["body"]:
    bail("skill-filesystem", "customSkillDirs does not reference EU4_DSH_SKILL_ROOT")

# Provenance: our tracked overlay must be the layer that patched that row.
if patch_path not in text:
    bail("patch-provenance", "profile overlay %s is not in the composed tree" % patch_path)

# The tools plugin must spawn the interpreter this preflight resolved. The
# composed row may carry the evaluated absolute path or reference the
# EU4_DSH_PYTHON variable the launcher exported; a bare default is a mismatch.
tools_rows = [e for e in entries if e["id"] == "eu4-tools"]
if not tools_rows:
    bail("python", "eu4-tools row is absent from the effective profile")
if python_path not in tools_rows[0]["body"] and "EU4_DSH_PYTHON" not in tools_rows[0]["body"]:
    bail("python", "eu4-tools python does not resolve to %s" % python_path)
PY

# Version pins recorded in runtime.json.
"$EU4_DSH_PYTHON" - "$RUNTIME" "$(dsh --version 2>/dev/null | tail -1)" "$(node --version)" "$(pnpm --version 2>/dev/null || echo unknown)" <<'PY' || exit 1
import json, sys
runtime = json.load(open(sys.argv[1], encoding="utf-8"))
dsh_v, node_v, pnpm_v = sys.argv[2:5]
checks = [
    ("dsh", runtime["dsh"]["version"], dsh_v),
    ("node", runtime["node"]["version"], node_v),
    ("pnpm", runtime["pnpm"]["version"], pnpm_v),
]
bad = [(n, w, a) for n, w, a in checks if w != a]
if bad:
    print(json.dumps({"ok": False, "stage": "version-pin",
                      "detail": "; ".join("%s pinned %s, found %s" % b for b in bad)}))
    sys.exit(1)
PY

SKILL=$INFRA/.opencode/skills/opengl-metal-reconstruction/SKILL.md
[ -f "$SKILL" ] || fail skill-bytes "skill file missing: $SKILL"

# Both local plugins must resolve from the profile root and expose a cordis
# plugin shape. web-app disables tool-goal/command-goal on the host plane
# because presets own them, so the plugin-id check above accepts an enabled
# preset copy for those two.
( cd "$PROFILE_DIR" && node --input-type=module -e '
for (const name of ["@eu4/dsh-goal", "@eu4/dsh-tools"]) {
  let m
  try {
    m = await import(name)
  } catch (error) {
    throw new Error(`${name}: ${error.message}`)
  }
  if (typeof m.apply !== "function") throw new Error(`${name}: apply() missing`)
  if (typeof m.name !== "string") throw new Error(`${name}: name missing`)
  if (!Array.isArray(m.inject)) throw new Error(`${name}: inject missing`)
}
' ) || fail plugin-resolve "a local EU4 plugin does not resolve to a cordis plugin from $PROFILE_DIR"

SKILL_SHA=$(shasum -a 256 "$SKILL" | cut -d' ' -f1)
PYTHON_VERSION=$("$EU4_DSH_PYTHON" -c 'import platform; print(platform.python_version())')
printf '{"ok":true,"profile":"%s","dsh_home":"%s","dsh_version":"%s","node":"%s","python":"%s","python_version":"%s","skill_root":"%s","skill_sha256":"%s"}\n' \
  "$PROFILE" "$DSH_HOME" "$(dsh --version 2>/dev/null | tail -1)" "$(node --version)" \
  "$EU4_DSH_PYTHON" "$PYTHON_VERSION" "$EU4_DSH_SKILL_ROOT" "$SKILL_SHA"
