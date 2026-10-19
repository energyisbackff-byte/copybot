#!/bin/bash
# ROOT-OWNED, FROZEN app installer for /update (installed only by `copybot system-update` or install.sh).
# Usage (by copybot-selfupdate only): app-update.sh SRC_DIR VERSION
# The downloaded code is treated as DATA: nothing from it ever runs as root, and root never writes into /opt/copybot
# (the bot user owns that folder). Every check and every copy runs as the bot's own user 'copybot'. Only the bot's own
# files change. The wallet guards, root helpers, sudoers, services and the console command are never touched: a newer
# version of those waits in /var/lib/copybot/pending for the owner (`copybot pending | guard-update | system-update`,
# each confirmed on the console by typing the version code).
# Steps: 1 pre-flight as copybot -> abort, nothing touched | 2 snapshot | 3 copy the bot's files | 4 restart + health
# check | 5 unhealthy -> restore the snapshot, restart, plain Telegram alert.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
SRC=${1:?source dir}; VERSION=${2:?version}
[[ "$VERSION" =~ ^[0-9A-Za-z._-]{1,64}$ ]] || { echo "bad version"; exit 1; }
. "${CB_LIBDIR:-/usr/local/lib/copybot}/lib.sh"
cd /
[ -d $APP ] || { echo "not installed - run install.sh"; exit 1; }
[ -f "$SRC/main.py" ] && [ -f "$SRC/config.yaml" ] || { echo "downloaded code looks incomplete"; exit 1; }
exec 9>"$LOCK"; flock -w 300 9 || { echo "another update is running"; exit 1; }
FROM=$(cat $APP/VERSION 2>/dev/null || echo unknown)
AS(){ cb_as "$@"; }
echo "== update ${FROM:0:7} -> ${VERSION:0:7} (bot code only)"

echo "== pre-flight as the bot user"
STG=$(mktemp -d /tmp/copybot-stage.XXXXXX); trap 'rm -rf "$STG"' EXIT
NEW=$STG/new; PF=$STG/pf
mkdir -p "$NEW" "$PF"; cp -r "$SRC"/. "$NEW"/; cp -r "$SRC"/. "$PF"/
id copybot >/dev/null 2>&1 && chown -R copybot:copybot "$STG"
AS cp $APP/config.yaml "$PF/config.yaml" 2>/dev/null || true
pf(){ echo "RESULT: preflight_failed"; cb_status preflight_failed "$VERSION" "$FROM" "$1"
  cb_tg "[WARN] ZETRAX update to ${VERSION:0:7} ABORTED in pre-flight. Nothing was changed, still running ${FROM:0:7}.
$1"; exit 1; }
out=$(AS $APP/.venv/bin/pip install -q --no-cache-dir -r "$PF/requirements.txt" 2>&1 >/dev/null) || pf "pip install failed: ${out: -300}"
out=$(cd "$PF" && AS $APP/.venv/bin/python -m py_compile "$PF"/*.py "$PF"/dashboard/*.py 2>&1) || pf "compile error: ${out: -500}"
out=$(cd "$PF" && AS $APP/.venv/bin/python migrate_config.py config.yaml config.yaml 2>&1) || pf "config migration failed: ${out: -300}"
out=$(cd "$PF" && AS env COPYBOT_DB="$PF/pf.db" COPYBOT_NO_TELEGRAM=1 TELEGRAM_BOT_TOKEN= timeout 60 $APP/.venv/bin/python - 2>&1 <<'PY'
import glob,os,sys,importlib;sys.path.insert(0,os.getcwd())
for f in sorted(glob.glob("*.py")):
    m=f[:-3]
    if m in("migrate_config",):continue
    importlib.import_module(m)
import notify,tgfmt;notify.keys_status();tgfmt.term("t",["x"]);print("imports ok")
PY
) || pf "import test failed: ${out: -500}"
echo "$out" | tail -1

echo "== snapshot"
SNAP=$(cb_snapshot); echo "snapshot $SNAP (${FROM:0:7})"
rollback(){ trap - ERR; set +e
  echo "RESULT: rolled_back ($1)"; E=$(cb_jtail 15); R=$(cb_restore 2>&1); T=$(cb_now); cb_restart
  if cb_health "$T" 90 0; then ST="bot is running ${FROM:0:7} again"; else ST="WARNING: bot still unhealthy after rollback"; fi
  cb_status rolled_back "$FROM" "$VERSION" "$1 | $E"
  cb_tg "[ERR] ZETRAX update to ${VERSION:0:7} ROLLED BACK.
reason: $1
$R - $ST. PAPER / NO real trades.
log tail:
$E"; exit 1; }
trap 'rollback "install step failed at line $LINENO"' ERR

echo "== installing the bot's code (as the bot user)"
AS env APP="$APP" NEW="$NEW" VERSION="$VERSION" bash -euo pipefail -c '
cp "$NEW"/*.py "$APP"/
for f in requirements.txt README.md Trading_Context.md HANDOFF.md install.sh update.sh; do if [ -f "$NEW/$f" ]; then cp "$NEW/$f" "$APP"/; fi; done
mkdir -p "$APP"/dashboard "$APP"/systemd "$APP"/system "$APP"/signer "$APP"/perpsguard "$APP"/tests "$APP"/docs
cp "$NEW"/dashboard/{server.py,index.html,room.js,update_menu.py} "$APP"/dashboard/
# reference copies only (the bot imports signer/solwire.py for transaction ids); nothing here is installed as root
for d in system systemd signer perpsguard tests docs; do
  if [ -d "$NEW/$d" ]; then find "$NEW/$d" -maxdepth 1 -type f -exec cp {} "$APP/$d/" \;; fi
done
[ -f "$APP"/config.yaml ] || cp "$NEW"/config.yaml "$APP"/config.yaml
cp "$NEW"/config.yaml "$APP"/config.default.yaml; rm -f "$APP"/LIVE_CONFIRM
cd "$APP" && "$APP"/.venv/bin/python "$APP"/migrate_config.py "$APP"/config.yaml "$APP"/config.default.yaml
sed -i "s/^live_trading:.*/live_trading: false         # old copy-trading stub, unused (real money = \/live)/" "$APP"/config.yaml
printf "%s\n" "$VERSION" > "$APP"/VERSION'
cb_perms

echo "== guards + system pieces: never changed by /update"
cb_stage_pending "$SRC" "$VERSION" || true

echo "== restart + health check"
T=$(cb_now); cb_restart
if ! cb_health "$T" "${CB_HEALTH_TIMEOUT:-90}" 1; then rollback "bot not healthy within ${CB_HEALTH_TIMEOUT:-90}s (service down or no heartbeat)"; fi
trap - ERR
cb_status ok "$VERSION" "$FROM" "healthy"
echo "RESULT: ok"
echo "Updated ${FROM:0:7} -> ${VERSION:0:7} (bot code only). PAPER MODE ONLY."
