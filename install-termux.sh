#!/data/data/com.termux/files/usr/bin/bash
# Install mamc, talk and ccnotify in Termux. One command:
#   curl -fsSL https://raw.githubusercontent.com/markoboskoauroville/termux-tools/main/install-termux.sh | bash
#
#   bash install-termux.sh          clone or pull ~/termux-tools, then the link step
#   bash install-termux.sh link     only the link step, from the checkout this file is in
#                                   (what mamc's u key runs after git pull)
#
# The link step: mamc runs FROM the checkout (the wrapper on the PATH names it, so a git pull
# is the update); the talker and the notifier are copied to ~/.talker and ~/.ccnotify, because
# the hooks in ~/.claude/settings.json name those paths. Every file on the PATH is written
# beside its name and renamed over it, never truncated (modules/termux-app.md §4).
# Absolute paths, no cd (modules/termux-proot-working.md §6).
set -euo pipefail
REPO_URL="https://github.com/markoboskoauroville/termux-tools.git"
INSTALL_DIR="${TT_INSTALL_DIR:-$HOME/termux-tools}"
# TT_BIN, TT_LOCAL_BIN and TT_HOME exist for the tests, which install into a folder of their own
# and must never rewrite the real commands on the PATH (four-tests.md: exclude your own sabotage)
BIN="${TT_BIN:-${PREFIX:-/data/data/com.termux/files/usr}/bin}"
LOCAL_BIN="${TT_LOCAL_BIN:-$HOME/.local/bin}"
STATE_HOME="${TT_HOME:-$HOME}"
if [ -t 1 ]; then AM="\033[38;5;214m"; OK="\033[1;32m"; BAD="\033[1;31m"; DIM="\033[0;90m"; OFF="\033[0m"; else AM=""; OK=""; BAD=""; DIM=""; OFF=""; fi
SHEBANG='#!/data/data/com.termux/files/usr/bin/bash'

put() {   # put <dir> <name> <body>  -- write beside the name, rename over it
  mkdir -p "$1" 2>/dev/null || return 0
  rm -f "$1/$2.new"
  printf '%s\n' "$3" > "$1/$2.new" && chmod 755 "$1/$2.new" && mv -f "$1/$2.new" "$1/$2"
  printf "  ${AM}%-10s${OFF} -> %s\n" "$2" "$1/$2"
}

link_step() {
  local here="$1"
  [ -f "$here/mamc/mamc.py" ] || { printf "  ${BAD}no mamc/mamc.py in $here${OFF}\n"; exit 1; }
  for d in "$BIN" "$LOCAL_BIN"; do
    put "$d" mamc    "$SHEBANG
# mamc - browse, run and uninstall the apps on this phone. Runs from the checkout: git pull updates it.
exec python3 \"$here/mamc/mamc.py\" \"\$@\""
    put "$d" talk    "$SHEBANG
exec python3 \"\$HOME/.talker/talker.py\" \"\$@\""
    put "$d" ccnotify "$SHEBANG
exec python3 \"\$HOME/.ccnotify/ccnotify.py\" \"\$@\""
    put "$d" ccpush  "$SHEBANG
# ccpush TEXT - push a message into the running Claude Code session.
exec python3 \"\$HOME/.ccnotify/ccnotify.py\" push \"\$@\""
  done
  mkdir -p "$STATE_HOME/.mamc" "$STATE_HOME/.talker" "$STATE_HOME/.ccnotify"
  # the copy of mamc.py that the first installs left in ~/.mamc is stale from now on: one program,
  # one place (modules/no-old-versions.md)
  if [ -f "$STATE_HOME/.mamc/mamc.py" ]; then rm -f "$STATE_HOME/.mamc/mamc.py"; printf "  ${DIM}removed the old copy ~/.mamc/mamc.py; mamc runs from $here${OFF}\n"; fi
  for f in "$here"/talker/*.py; do
    cp "$f" "$STATE_HOME/.talker/$(basename "$f").new" && mv -f "$STATE_HOME/.talker/$(basename "$f").new" "$STATE_HOME/.talker/$(basename "$f")"
  done
  cp "$here/notify/ccnotify.py" "$STATE_HOME/.ccnotify/ccnotify.py.new" && mv -f "$STATE_HOME/.ccnotify/ccnotify.py.new" "$STATE_HOME/.ccnotify/ccnotify.py"
  local v; v="$(sed -nE 's/^APP_VERSION *= *([0-9]+).*/\1/p' "$here/version.py" 2>/dev/null || true)"
  printf "  ${OK}done${OFF}  termux-tools v${v:-?}: mamc, talk, ccnotify, ccpush on the PATH\n"
}

if [ "${1:-}" = "link" ]; then
  SOURCE="${BASH_SOURCE[0]}"
  HERE="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
  link_step "$HERE"
  exit 0
fi

printf "\n  ${AM}termux-tools${OFF}  mamc, talk and ccnotify\n\n"
MISSING=""
printf "  %-12s " "python";  command -v python3 >/dev/null && printf "${OK}ok${OFF} $(python3 --version 2>&1)\n" || { printf "${BAD}MISSING${OFF}\n"; MISSING="$MISSING python"; }
printf "  %-12s " "git";     command -v git >/dev/null && printf "${OK}ok${OFF}\n" || { printf "${BAD}MISSING${OFF}\n"; MISSING="$MISSING git"; }
printf "  %-12s " "mpv";     command -v mpv >/dev/null && printf "${OK}ok${OFF}\n" || printf "${DIM}not found: talk needs it (pkg install mpv)${OFF}\n"
printf "  %-12s " "termux-api"; command -v termux-notification >/dev/null && printf "${OK}ok${OFF}\n" || printf "${DIM}not found: ccnotify's bubbles need it (pkg install termux-api, and the Termux:API app)${OFF}\n"
if [ -n "$MISSING" ]; then pkg install -y $MISSING; fi

if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull -q --ff-only origin main || { printf "  ${BAD}git pull failed${OFF}, nothing changed\n"; exit 1; }
else
  git clone -q "$REPO_URL" "$INSTALL_DIR" || { printf "  ${BAD}clone failed${OFF}: is the repository public, and is there a network?\n"; exit 1; }
fi
link_step "$INSTALL_DIR"
printf "\n  type  ${AM}mamc${OFF}  anywhere. The hooks for talk and ccnotify are in README.md.\n\n"
