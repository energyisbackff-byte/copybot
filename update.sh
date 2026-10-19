#!/bin/bash
# CONSOLE / ONE-TIME script (root). Since 19 Oct 2026 /update no longer runs this file: it runs the frozen, root-owned
# /usr/local/lib/copybot/app-update.sh (system/copybot-app-update.sh), which installs the bot's own code only and never
# runs downloaded code as root.
# This script is what the OLD self-updater runs one last time, and what you can run by hand on the console:
#   1 installs the root helpers, services and the console command from this folder (cb_install_system)
#   2 hands over to the frozen installer for the bot's code (pre-flight, snapshot, install, health check, rollback)
# The wallet guards (signer, coin guard, perps guard) are NOT installed here: a newer version waits for
# `copybot guard-update`, which asks you to type the version code.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
. "$SRC/system/copybot-lib.sh"
[ -d $APP ] || { echo "not installed - run install.sh"; exit 1; }
VERSION="${COPYBOT_VERSION:-$(cb_gh_sha)}"
echo "== system pieces (root helpers, services, console command)"
cb_install_system "$SRC"
echo "== the bot's code (frozen installer; wallet guards are never changed here)"
exec bash "$LIBDIR/app-update.sh" "$SRC" "$VERSION"
