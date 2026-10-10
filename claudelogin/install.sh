#!/data/data/com.termux/files/usr/bin/bash
# Install claudelogin in Termux, and ONLY claudelogin. One command (README.md has it with a copy button):
#   curl -fsSL https://raw.githubusercontent.com/markoboskoauroville/termux-tools/main/claudelogin/install.sh | bash
#
# It fetches the claudelogin/ folder alone into ~/claudelogin (a sparse checkout of termux-tools:
# mamc, talk and ccnotify are not downloaded, and an existing ~/termux-tools is never touched).
#
#   bash install.sh          clone or pull the checkout, the Python packages, then the link step
#   bash install.sh link     only the link step, from the checkout this file is in
#                            (what the u key runs after git pull)
#
# The link step writes `claudelogin` into $PREFIX/bin and ~/.local/bin. The command runs FROM the
# checkout, so a git pull is the update. It resolves home when it RUNS, never at install time:
# $PREFIX/bin is read from Termux and from inside the PRoot, which spell home two ways
# (termux-proot-working.md, "A command in $PREFIX/bin cannot hold a home path"). Every file on the
# PATH is written beside its name and renamed over it, never truncated (termux-app.md §4).
set -euo pipefail
REPO_URL="${CL_REPO_URL:-https://github.com/markoboskoauroville/termux-tools.git}"
BRANCH="${CL_BRANCH:-main}"
INSTALL_DIR="${CL_INSTALL_DIR:-$HOME/claudelogin}"
# CL_BIN and CL_LOCAL_BIN exist for the tests, which must never rewrite the real command
BIN="${CL_BIN:-${PREFIX:-/data/data/com.termux/files/usr}/bin}"
LOCAL_BIN="${CL_LOCAL_BIN:-$HOME/.local/bin}"
if [ -t 1 ]; then AM="\033[38;5;214m"; OK="\033[1;32m"; BAD="\033[1;31m"; DIM="\033[0;90m"; OFF="\033[0m"; else AM=""; OK=""; BAD=""; DIM=""; OFF=""; fi
SHEBANG='#!/data/data/com.termux/files/usr/bin/bash'

put() {   # put <dir> <name> <body>  -- write beside the name, rename over it
  mkdir -p "$1" 2>/dev/null || return 0
  rm -f "$1/$2.new"
  printf '%s\n' "$3" > "$1/$2.new" && chmod 755 "$1/$2.new" && mv -f "$1/$2.new" "$1/$2"
  printf "  ${AM}%-12s${OFF} -> %s\n" "$2" "$1/$2"
}

link_step() {
  local here="$1" where
  [ -f "$here/claudelogin.py" ] || { printf "  ${BAD}no claudelogin.py in $here${OFF}\n"; exit 1; }
  # under home: written as a path relative to home, found again by whoever runs it
  case "$here" in
    "$HOME"/*) where="${here#"$HOME"/}" ;;
    /data/data/com.termux/files/home/*) where="${here#/data/data/com.termux/files/home/}" ;;
    /root/*) where="${here#/root/}" ;;
    *) where="" ;;
  esac
  local body
  if [ -n "$where" ]; then
    body="$SHEBANG
# claudelogin - sign Claude Code in: the link opens in Firefox, the code is pasted for you.
# Home is found when this runs: Termux and the PRoot spell it two ways.
for _h in \"\$HOME\" /data/data/com.termux/files/home /root; do
  if [ -n \"\$_h\" ] && [ -f \"\$_h/$where/claudelogin.py\" ]; then
    exec \"\$(command -v python3 || command -v python)\" \"\$_h/$where/claudelogin.py\" \"\$@\"
  fi
done
echo \"claudelogin: not found in \$HOME/$where, /data/data/com.termux/files/home/$where or /root/$where.\" >&2
echo \"Reinstall with the line in claudelogin/README.md.\" >&2
exit 1"
  else
    body="$SHEBANG
# claudelogin - sign Claude Code in: the link opens in Firefox, the code is pasted for you.
exec \"\$(command -v python3 || command -v python)\" \"$here/claudelogin.py\" \"\$@\""
  fi
  for d in "$BIN" "$LOCAL_BIN"; do put "$d" claudelogin "$body"; done
  local v; v="$(sed -nE 's/^APP_VERSION *= *([0-9]+).*/\1/p' "$here/version.py" 2>/dev/null || true)"
  printf "  ${OK}done${OFF}  claudelogin v${v:-?} on the PATH\n"
}

if [ "${1:-}" = "link" ]; then
  HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
  link_step "$HERE"
  exit 0
fi

printf "\n  ${AM}claudelogin${OFF}  one word signs Claude Code in\n\n"
MISSING=""
row() { printf "  %-14s " "$1"; }
row python;   if command -v python3 >/dev/null || command -v python >/dev/null; then printf "${OK}ok${OFF}\n"; else printf "${BAD}MISSING${OFF}\n"; MISSING="$MISSING python"; fi
row git;      if command -v git >/dev/null; then printf "${OK}ok${OFF}\n"; else printf "${BAD}MISSING${OFF}\n"; MISSING="$MISSING git"; fi
row claude;   if command -v claude >/dev/null; then printf "${OK}ok${OFF}\n"; else printf "${DIM}not found: install Claude Code first (claude-code-termux.md)${OFF}\n"; fi
row termux-api; if command -v termux-clipboard-get >/dev/null; then printf "${OK}ok${OFF}\n"; else printf "${DIM}not found: the code is then pasted by hand (pkg install termux-api, and the Termux:API app)${OFF}\n"; MISSING="$MISSING termux-api"; fi
row firefox
if command -v pm >/dev/null && pm list packages org.mozilla 2>/dev/null | grep -qE 'package:org\.mozilla\.(firefox|firefox_beta|fenix)$'; then
  printf "${OK}ok${OFF}\n"
else
  printf "${DIM}not found: the link opens in the default browser${OFF}\n"
fi
if [ -n "$MISSING" ] && command -v pkg >/dev/null; then pkg install -y $MISSING; fi
PY="$(command -v python3 || command -v python)"
row flask
if "$PY" -c 'import flask, waitress' 2>/dev/null; then printf "${OK}ok${OFF}\n"; else
  printf "installing\n"; "$PY" -m pip install flask waitress || printf "  ${DIM}pip failed: claudelogin still works, without its page${OFF}\n"
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull -q --ff-only origin "$BRANCH" || { printf "  ${BAD}git pull failed${OFF}, nothing changed\n"; exit 1; }
elif [ -e "$INSTALL_DIR" ]; then
  printf "  ${BAD}$INSTALL_DIR exists and is not a checkout${OFF}: move it away, nothing changed\n"; exit 1
else
  # nothing on disk until the clone is whole: clone beside the name, rename into place
  rm -rf "$INSTALL_DIR.new"
  # --filter=blob:none: the other apps' files are never downloaded (a server that cannot filter
  # sends them and git says so; the sparse checkout below still keeps them off the disk)
  if ! git clone -q --filter=blob:none --no-checkout -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR.new" 2>/dev/null \
     && ! git clone -q --no-checkout -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR.new"; then
    rm -rf "$INSTALL_DIR.new"
    printf "  ${BAD}clone failed${OFF}: is there a network? (a private repository also needs gh auth login)\n"; exit 1
  fi
  git -C "$INSTALL_DIR.new" sparse-checkout set --no-cone '/claudelogin/' \
    && git -C "$INSTALL_DIR.new" checkout -q "$BRANCH" \
    || { rm -rf "$INSTALL_DIR.new"; printf "  ${BAD}checkout failed${OFF}, nothing changed\n"; exit 1; }
  mv "$INSTALL_DIR.new" "$INSTALL_DIR"
fi
link_step "$INSTALL_DIR/claudelogin"
printf "\n  type  ${AM}claudelogin${OFF}  anywhere.\n\n"
