#!/bin/bash
# Root watchdog (systemd timer, every 2 min). Independent of bot code.
# Unhealthy = unit failed / restart-looping / heartbeat stale >5 min. Alerts at most every 30 min,
# auto-rolls back once if the failure began within 30 min of a successful update, alerts once on recovery.
set -uo pipefail
. "${CB_LIBDIR:-/usr/local/lib/copybot}/lib.sh"
exec 9>"$LOCK"; flock -n 9 || exit 0          # update/rollback in progress
W=$STATE/watchdog; mkdir -p "$W"; now=$(cb_now)
rd(){ cat "$W/$1" 2>/dev/null || echo "${2:-}"; }
ST=$(systemctl show -p ActiveState --value copybot 2>/dev/null); SUB=$(systemctl show -p SubState --value copybot 2>/dev/null)
RES=$(systemctl show -p Result --value copybot 2>/dev/null); NR=$(systemctl show -p NRestarts --value copybot 2>/dev/null); NR=${NR:-0}
PNR=$(rd nrestarts "$NR"); echo "$NR" > "$W/nrestarts"
UPS=$(systemctl show -p ActiveEnterTimestamp --value copybot 2>/dev/null); UP=$(( now - $(date -d "${UPS:-now}" +%s 2>/dev/null || echo "$now") ))
bad=""
if [ "$ST" = failed ] || [ "$SUB" = auto-restart ] || { [ "$ST" != active ] && [ "$RES" != success ]; }; then bad="service $ST/$SUB ($RES)"
elif [ $(( NR - PNR )) -ge 2 ]; then bad="restart loop (+$((NR-PNR)) restarts since last check)"
elif [ "$ST" = active ] && [ "$UP" -gt 300 ] && grep -q COPYBOT_HEARTBEAT "$APP/notify.py" 2>/dev/null; then
  age=$(cb_hb_age); { [ "$age" -lt 0 ] || [ "$age" -gt 300 ]; } && bad="heartbeat stale (${age}s, listener down?)"
fi
if [ -z "$bad" ]; then
  if [ -n "$(rd bad_since)" ]; then cb_tg "[OK] 🟢 ZETRAX watchdog: bot recovered ($(cat "$APP/VERSION" 2>/dev/null | cut -c1-7)). PAPER / NO real trades."; fi
  rm -f "$W/bad_since" "$W/last_alert"; exit 0
fi
since=$(rd bad_since "$now"); echo "$since" > "$W/bad_since"
VER=$(cat "$APP/VERSION" 2>/dev/null || echo unknown)
URES=$(cb_status_get result); UTS=$(cb_status_get ts); UTS=${UTS%.*}; UTS=${UTS:-0}
if [ "$URES" = ok ] && [ $(( since - UTS )) -le 1800 ] && [ "$(rd rolled_for)" != "$VER" ] && [ -f "$BK/last/code.tgz" ]; then
  echo "$VER" > "$W/rolled_for"; E=$(cb_jtail)
  if out=$(cb_restore 2>&1); then T=$(cb_now); cb_restart; NEW=$(cat "$APP/VERSION" 2>/dev/null)
    if cb_health "$T" 90 0; then cb_status rolled_back "$NEW" "$VER" "watchdog: $bad"; R="rolled back ${VER:0:7} -> ${NEW:0:7}, bot running"
    else cb_status rollback_unhealthy "$NEW" "$VER" "watchdog: $bad"; R="rolled back to ${NEW:0:7} but still unhealthy"; fi
  else R="auto-rollback failed: $out"; fi
  cb_tg "[ERR] 🔴 ZETRAX watchdog: $bad right after update.
$R.
log tail:
$E"; echo "$now" > "$W/last_alert"; exit 0
fi
if [ $(( now - $(rd last_alert 0) )) -ge 1800 ]; then
  cb_tg "[ERR] 🔴 ZETRAX watchdog: $bad (since $(date -d @"$since" '+%H:%M')). No auto-rollback (not right after an update). Try /rollback or 'copybot logs'.
log tail:
$(cb_jtail)"; echo "$now" > "$W/last_alert"
fi
