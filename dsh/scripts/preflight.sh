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

python3 - "$DUMP" "$RUNTIME" "$PATCH" "$EU4_DSH_SKILL_ROOT" <<'PY' || exit 1
import json, re, sys

dump_path, runtime_path, patch_path, skill_root = sys.argv[1:5]
text = open(dump_path, encoding="utf-8").read()
runtime = json.load(open(runtime_path, encoding="utf-8"))


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


# Every native goal service must be mounted, and enabled somewhere: web-app
# disables tool-goal/command-goal at the host plane because presets own them,
# so an enabled preset copy is the correct state for those two.
required = ["goal", "goal-round-driver", "tool-goal", "command-goal", "eu4-goal"]
for name in required:
    matches = [e for e in entries if e["id"] == name]
    if not matches:
        bail(name, "required plugin row '%s' absent from the effective profile" % name)
    if not any(enabled(e) for e in matches):
        bail(name, "plugin row '%s' is present but disabled everywhere" % name)

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

print(json.dumps({"ok": True}))
PY

# Version pins recorded in runtime.json.
python3 - "$RUNTIME" "$(dsh --version 2>/dev/null | tail -1)" "$(node --version)" "$(pnpm --version 2>/dev/null || echo unknown)" <<'PY' || exit 1
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

# The plugin must resolve from the profile root and expose a cordis plugin shape.
( cd "$PROFILE_DIR" && node --input-type=module -e '
const m = await import("@eu4/dsh-goal")
if (typeof m.apply !== "function") throw new Error("apply() missing")
if (typeof m.name !== "string") throw new Error("name missing")
if (!Array.isArray(m.inject)) throw new Error("inject missing")
' ) || fail plugin-resolve "@eu4/dsh-goal does not resolve to a cordis plugin from $PROFILE_DIR"

SKILL_SHA=$(shasum -a 256 "$SKILL" | cut -d' ' -f1)
printf '{"ok":true,"profile":"%s","dsh_home":"%s","dsh_version":"%s","node":"%s","skill_root":"%s","skill_sha256":"%s"}\n' \
  "$PROFILE" "$DSH_HOME" "$(dsh --version 2>/dev/null | tail -1)" "$(node --version)" "$EU4_DSH_SKILL_ROOT" "$SKILL_SHA"
