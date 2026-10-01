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
POWER_HELPER=/usr/local/libexec/eu4-power-mode
RULE=/etc/sudoers.d/eu4-powermetrics
STAGED=$(mktemp /private/tmp/eu4-sudoers.XXXXXX)
trap 'rm -f "$STAGED"' EXIT HUP INT TERM
printf '%s ALL=(root) NOPASSWD: %s, %s powermode 0, %s powermode 1, %s powermode 2, %s lowpowermode 0, %s lowpowermode 1\n' \
  "$SUDO_USER" "$HELPER" "$POWER_HELPER" "$POWER_HELPER" "$POWER_HELPER" "$POWER_HELPER" "$POWER_HELPER" > "$STAGED"
/usr/sbin/visudo -cf "$STAGED"
/usr/bin/install -d -o root -g wheel -m 0755 /usr/local/libexec
/usr/bin/install -o root -g wheel -m 0755 "$SOURCE_DIR/powermetrics_helper" "$HELPER"
/usr/bin/install -o root -g wheel -m 0755 "$SOURCE_DIR/power_mode_helper" "$POWER_HELPER"
/usr/bin/install -o root -g wheel -m 0440 "$STAGED" "$RULE"
/usr/sbin/visudo -cf /etc/sudoers
echo "Installed the powermetrics and allowlisted power-mode helpers for $SUDO_USER"
