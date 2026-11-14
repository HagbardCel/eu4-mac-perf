#!/bin/sh
# Idempotent provisioning of the eu4-web DSH profile.
#
# Creates (once) and then synchronizes a dedicated DSH_HOME so the EU4 profile
# never mutates ~/.dsh, another profile, or any existing session. Re-running is
# safe: it re-syncs the tracked overlay and leaves sessions/settings alone.
#
#   ./dsh/scripts/provision.sh [--init]
#
# --init forces the one-time creation from the shipped web template. This is the
# only mode that passes --from-default-profile; passing it later would replace
# the profile and its local configuration.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)
PROFILE=eu4-web
PLUGIN=$INFRA/dsh/eu4-goal-plugin

# The dedicated home is deliberate. An inherited DSH_HOME (the interactive
# session's ~/.dsh) must never be reused: this profile owns its own sessions,
# plugins and settings, and must not touch the user's normal installation.
: "${EU4_DSH_HOME:=$HOME/.local/share/eu4-dsh}"
DSH_HOME=$EU4_DSH_HOME
export DSH_HOME
PROFILE_DIR=$DSH_HOME/profiles/$PROFILE
OVERLAY=$INFRA/dsh/profile/$PROFILE

INIT=0
FORCE_CREDS=0
for arg in "$@"; do
  case "$arg" in
    --init) INIT=1 ;;
    --refresh-credentials) FORCE_CREDS=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "provision: unknown option: $arg" >&2; exit 2 ;;
  esac
done

command -v dsh >/dev/null 2>&1 || { echo "provision: dsh not on PATH" >&2; exit 1; }

mkdir -p "$DSH_HOME/profiles"

if [ ! -d "$PROFILE_DIR" ]; then
  INIT=1
fi

if [ "$INIT" -eq 1 ]; then
  echo "provision: initializing profile '$PROFILE' from the shipped web template"
  dsh --profile "$PROFILE" --from-default-profile web --dump-config >/dev/null
else
  echo "provision: profile '$PROFILE' exists; synchronizing overlay only"
fi

[ -d "$PROFILE_DIR" ] || { echo "provision: profile dir missing after init" >&2; exit 1; }

install -m 0644 "$OVERLAY/cordis.patch.yml" "$PROFILE_DIR/cordis.patch.yml"
echo "provision: synced cordis.patch.yml"

# Share the host installation's credentials with this dedicated home. The
# profile's provider reads its key from OPENCODE_GO_API_KEY or this file; without
# either, the web UI asks for an API key. Copying (rather than symlinking) keeps
# a UI-saved key from writing back into the host's credential store.
HOST_HOME=${EU4_DSH_HOST_HOME:-$HOME/.dsh}
HOST_CREDS=$HOST_HOME/.credentials.yaml
CREDS=$DSH_HOME/.credentials.yaml
have_keys=0
if [ -f "$CREDS" ] && grep -qE 'OPENCODE_GO_API_KEY|DEEPSEEK_API_KEY' "$CREDS" 2>/dev/null; then
  have_keys=1
fi
if [ -f "$HOST_CREDS" ] && { [ "$FORCE_CREDS" -eq 1 ] || [ "$have_keys" -eq 0 ]; }; then
  install -m 0600 "$HOST_CREDS" "$CREDS"
  echo "provision: copied host credentials into $DSH_HOME"
elif [ "$have_keys" -eq 1 ]; then
  echo "provision: credentials already present (use --refresh-credentials to re-copy)"
else
  echo "provision: no host credentials found at $HOST_CREDS" >&2
  echo "provision: export OPENCODE_GO_API_KEY in the shell that runs run.sh" >&2
fi

if [ ! -e "$PROFILE_DIR/node_modules/@eu4/dsh-goal" ]; then
  echo "provision: installing plugin via link:$PLUGIN"
  dsh plugin --profile "$PROFILE" add "link:$PLUGIN"
else
  echo "provision: goal plugin already installed"
fi

TOOLS_PLUGIN=${EU4_DSH_TOOLS_PLUGIN:-$(cd "$(dirname "$0")/.." && pwd)/eu4-tools-plugin}
if [ ! -e "$PROFILE_DIR/node_modules/@eu4/dsh-tools" ]; then
  echo "provision: installing tools plugin via link:$TOOLS_PLUGIN"
  dsh plugin --profile "$PROFILE" add "link:$TOOLS_PLUGIN"
else
  echo "provision: tools plugin already installed"
fi

echo "provision: DSH_HOME=$DSH_HOME"
echo "provision: profile=$PROFILE_DIR"
