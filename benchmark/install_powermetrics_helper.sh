#!/bin/sh
# One-time setup. Invoke via: sudo sh benchmark/install_powermetrics_helper.sh
set -eu
if [ "$(id -u)" -ne 0 ] || [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = root ]; then
  echo "Run this from the intended user's session using sudo." >&2
  exit 1
fi
case "$SUDO_USER" in
  *[!a-zA-Z0-9_-]*|'') echo "Unsafe account name" >&2; exit 1 ;;
esac
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HELPER=/usr/local/libexec/eu4-powermetrics
RULE=/etc/sudoers.d/eu4-powermetrics
STAGED=$(mktemp /private/tmp/eu4-sudoers.XXXXXX)
trap 'rm -f "$STAGED"' EXIT HUP INT TERM
printf '%s ALL=(root) NOPASSWD: %s\n' "$SUDO_USER" "$HELPER" > "$STAGED"
/usr/sbin/visudo -cf "$STAGED"
/usr/bin/install -d -o root -g wheel -m 0755 /usr/local/libexec
/usr/bin/install -o root -g wheel -m 0755 "$SOURCE_DIR/powermetrics_helper" "$HELPER"
/usr/bin/install -o root -g wheel -m 0440 "$STAGED" "$RULE"
/usr/sbin/visudo -cf /etc/sudoers
echo "Installed $HELPER and validated the narrow sudoers rule for $SUDO_USER"
