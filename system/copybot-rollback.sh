#!/bin/bash
# Manual rollback (Telegram /rollback or `copybot rollback`): restore last code snapshot, restart, report.
set -uo pipefail
. "${CB_LIBDIR:-/usr/local/lib/copybot}/lib.sh"
exec 9>"$LOCK"; flock -w 120 9 || { cb_tg "ZETRAX rollback: another update/rollback is running, try later."; exit 1; }
CUR=$(cat "$APP/VERSION" 2>/dev/null || echo unknown)
if ! out=$(cb_restore 2>&1); then cb_status rollback_failed "$CUR" "$CUR" "$out"; cb_tg "[ERR] ZETRAX rollback failed: $out"; exit 1; fi
NEW=$(cat "$APP/VERSION" 2>/dev/null || echo unknown); T=$(cb_now); cb_restart
if cb_health "$T" 90 0; then cb_status rolled_back "$NEW" "$CUR" "manual rollback"; cb_tg "[OK] ZETRAX rolled back ${CUR:0:7} -> ${NEW:0:7}. Bot is running. PAPER / NO real trades."
else E=$(cb_jtail); cb_status rollback_unhealthy "$NEW" "$CUR" "$E"; cb_tg "[ERR] ZETRAX rolled back to ${NEW:0:7} but the service is not healthy.
$E"; exit 1; fi
