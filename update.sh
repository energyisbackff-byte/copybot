#!/bin/bash
# Self-healing update of /opt/copybot from this folder. Keeps .env, bot.db, dashboard token/url. The old copy-trading stub stays off; real money is the /live feature (available, off until you switch it on).
# 1 pre-flight (compile + import every module in a staging copy with the real venv) -> abort, nothing touched
# 2 snapshot current code -> 3 install -> 4 restart + health check (service active + fresh heartbeat)
# 5 unhealthy or any install error -> restore snapshot, restart, plain Telegram alert with masked log tail
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
. "$SRC/system/copybot-lib.sh"
[ -d $APP ] || { echo "not installed - run install.sh"; exit 1; }
exec 9>"$LOCK"; flock -w 300 9 || { echo "another update is running"; exit 1; }
FROM=$(cat $APP/VERSION 2>/dev/null || echo unknown)
VERSION="${COPYBOT_VERSION:-$(cb_gh_sha)}"
echo "== update ${FROM:0:7} -> ${VERSION:0:7}"

echo "== pre-flight in staging"
STG=$(mktemp -d /tmp/copybot-stage.XXXXXX); trap 'rm -rf "$STG"' EXIT
cp -r "$SRC"/. "$STG"/; cp $APP/config.yaml "$STG/config.yaml" 2>/dev/null || true
pf(){ echo "RESULT: preflight_failed"; cb_status preflight_failed "$VERSION" "$FROM" "$1"
  cb_tg "[WARN] ZETRAX update to ${VERSION:0:7} ABORTED in pre-flight. Nothing was changed, still running ${FROM:0:7}.
$1"; exit 1; }
$APP/.venv/bin/pip install -q -r "$STG/requirements.txt" >/dev/null 2>"$STG/pip.err" || pf "pip install failed: $(tail -c 300 "$STG/pip.err")"
out=$($APP/.venv/bin/python -m py_compile "$STG"/*.py "$STG"/dashboard/*.py "$STG"/signer/*.py "$STG"/perpsguard/*.py 2>&1) || pf "compile error: ${out: -500}"
out=$(cd "$STG" && $APP/.venv/bin/python migrate_config.py config.yaml config.yaml 2>&1) || pf "config migration failed: ${out: -300}"
out=$(cd "$STG" && COPYBOT_DB="$STG/pf.db" COPYBOT_NO_TELEGRAM=1 TELEGRAM_BOT_TOKEN= timeout 60 $APP/.venv/bin/python - 2>&1 <<'PY'
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

echo "== installing code"
cp "$SRC"/*.py $APP/
for f in requirements.txt README.md install.sh update.sh; do cp "$SRC/$f" $APP/; done
mkdir -p $APP/dashboard $APP/systemd $APP/system $APP/signer $APP/perpsguard $APP/tests $APP/docs
cp "$SRC"/dashboard/{server.py,index.html,room.js,update_menu.py} $APP/dashboard/
cp "$SRC"/systemd/*.service $APP/systemd/
find "$SRC/system" "$SRC/signer" "$SRC/perpsguard" "$SRC/tests" "$SRC/docs" -maxdepth 1 -type f -print0 2>/dev/null | while IFS= read -r -d "" f; do cp "$f" "$APP/$(basename "$(dirname "$f")")/"; done   # files only (a stray __pycache__ dir must not abort the install)
for f in Trading_Context.md; do [ -f "$SRC/$f" ] && cp "$SRC/$f" $APP/; done
[ -f $APP/config.yaml ] || cp "$SRC/config.yaml" $APP/
sed -i 's/^live_trading:.*/live_trading: false         # old copy-trading stub, unused (real money = \/live)/' $APP/config.yaml; rm -f $APP/LIVE_CONFIRM
cp "$SRC/config.yaml" $APP/config.default.yaml
$APP/.venv/bin/python $APP/migrate_config.py $APP/config.yaml $APP/config.default.yaml
echo "$VERSION" > $APP/VERSION
cb_units

if [ -z "${CB_TEST:-}" ]; then
  echo "== root helpers, sudoers, watchdog"
  install -d -m 0755 $LIBDIR
  install -o root -g root -m 0644 "$SRC/system/copybot-lib.sh" $LIBDIR/lib.sh
  install -o root -g root -m 0755 "$SRC/system/copybot-rollback.sh" $LIBDIR/rollback.sh
  install -o root -g root -m 0755 "$SRC/system/copybot-watchdog.sh" $LIBDIR/watchdog.sh
  for h in selfupdate:update setkey:setkey rollback:rollback; do
    bin=${h%%:*}; sf=${h##*:}
    install -o root -g root -m 0755 "$SRC/system/copybot-$bin" /usr/local/sbin/copybot-$bin
    install -o root -g root -m 0440 "$SRC/system/copybot-$sf.sudoers" /tmp/copybot-$sf.sudoers.new
    if visudo -cf /tmp/copybot-$sf.sudoers.new >/dev/null; then mv /tmp/copybot-$sf.sudoers.new /etc/sudoers.d/copybot-$sf; echo "sudoers copybot-$sf installed (validated)"
    else rm -f /tmp/copybot-$sf.sudoers.new; echo "WARNING: sudoers copybot-$sf failed validation, skipped"; fi
  done
  cp "$SRC"/system/copybot-watchdog.{service,timer} /etc/systemd/system/; systemctl daemon-reload; systemctl enable --now copybot-watchdog.timer >/dev/null 2>&1 || echo "WARNING: watchdog timer not enabled"
  echo "== live wallet signer (never blocks the update; paper trading works without it)"
  cb_install_signer "$SRC" || echo "WARNING: signer not installed; live trading unavailable, paper unaffected"
  echo "== perps guard (never blocks the update; paper perps work without it)"
  cb_install_perpsguard "$SRC" || echo "WARNING: perps guard not installed; real-money perps unavailable, paper unaffected"
  HOST=$(sed 's#https://##' $APP/dashboard/url.txt 2>/dev/null || true)
  cat > /usr/local/bin/copybot.new <<CMD
#!/bin/bash
case "\$1" in
 status) cd $APP && sudo -u copybot $APP/.venv/bin/python main.py status;;
 stop) systemctl stop copybot; echo stopped;;
 start) systemctl start copybot; echo started;;
 restart) systemctl restart copybot copybot-dashboard; echo restarted;;
 logs) journalctl -u copybot -n 60 --no-pager;;
 url) echo "https://$HOST/?t=\$(cat $APP/dashboard/token.txt)";;
 update) CB_FOREGROUND=1 exec /usr/local/sbin/copybot-selfupdate;;
 health) cd $APP && sudo -u copybot $APP/.venv/bin/python -c "import main;print(main.health_text(plain=True))" 2>/dev/null || systemctl --no-pager status copybot | head -5;;
 rollback) bash $LIBDIR/rollback.sh;;
 version) cat $APP/VERSION 2>/dev/null || echo unknown;;
 wallet) sudo -u copybot-signer env SIGNER_STATE=/var/lib/copybot-signer /usr/bin/python3 /usr/local/lib/copybot-signer/signer.py address;;
 sethome) [ -n "\$2" ] || { echo "usage: copybot sethome ADDRESS"; exit 1; }; sudo -u copybot-signer env SIGNER_STATE=/var/lib/copybot-signer /usr/bin/python3 /usr/local/lib/copybot-signer/signer.py sethome "\$2";;
 wallet-export) echo "WARNING: this prints the live wallet's PRIVATE KEY (for importing into Phantom if the server is lost)."; read -rp "type EXPORT to continue: " a; [ "\$a" = EXPORT ] && sudo -u copybot-signer env SIGNER_STATE=/var/lib/copybot-signer /usr/bin/python3 /usr/local/lib/copybot-signer/signer.py export;;
 signer) systemctl --no-pager status copybot-signer | head -8; journalctl -u copybot-signer -n 20 --no-pager -o cat;;
 perps-agent) sudo -u copybot-perps env PERPS_STATE=/var/lib/copybot-perps /opt/copybot-perpsguard/venv/bin/python /usr/local/lib/copybot-perpsguard/perpsguard.py address;;
 perps-master) [ -n "\$2" ] || { echo "usage: copybot perps-master 0xADDRESS"; exit 1; }; sudo -u copybot-perps env PERPS_STATE=/var/lib/copybot-perps /opt/copybot-perpsguard/venv/bin/python /usr/local/lib/copybot-perpsguard/perpsguard.py master "\$2" && systemctl restart copybot-perpsguard;;
 perps-guard) cat /etc/copybot-perpsguard.json; systemctl --no-pager status copybot-perpsguard | head -8; journalctl -u copybot-perpsguard -n 20 --no-pager -o cat;;
 perps-set) [ -n "\$3" ] || { echo "usage: copybot perps-set network test|main  |  copybot perps-set max_leverage 3  (max_order_usd, max_day_usd, max_orders_day)"; exit 1; }
   if [ "\$2" = network ] && [ "\$3" = main ]; then read -rp "MAINNET = REAL MONEY on Hyperliquid. Type MAINNET to confirm: " a; [ "\$a" = MAINNET ] || { echo "not changed"; exit 1; }; fi
   python3 - "\$2" "\$3" <<'PY' && systemctl restart copybot-perpsguard && echo "perps guard restarted"
import json,sys
k,v=sys.argv[1],sys.argv[2];p="/etc/copybot-perpsguard.json";c=json.load(open(p))
if k=="network":assert v in("test","main"),"network must be test or main"
elif k in("max_leverage","max_orders_day"):v=int(v);assert 1<=v<=50
elif k in("max_order_usd","max_day_usd"):v=float(v);assert 0<v<=100000
else:raise SystemExit("unknown setting")
c[k]=v;json.dump(c,open(p,"w"),indent=1);print("set",k,"=",v)
PY
   ;;
 ssh-allow) case "\$2" in
     keep) systemctl stop copybot-ssh-revert.timer 2>/dev/null; echo "kept: SSH only from the allowed IP";;
     open) ufw allow 22/tcp >/dev/null; for n in \$(ufw status numbered | grep -E "22/tcp.*ALLOW IN.*[0-9]+\\.[0-9]+" | grep -o "^\\[ *[0-9]*" | tr -d "[ " | sort -rn); do yes | ufw delete \$n >/dev/null; done; echo "SSH open to everyone again";;
     *) IP="\$2"; [[ "\$IP" =~ ^[0-9]{1,3}(\\.[0-9]{1,3}){3}\$ ]] || { echo "usage: copybot ssh-allow YOUR.IP | keep | open   (your IP: the address you SSH from)"; exit 1; }
        ufw allow from "\$IP" to any port 22 proto tcp >/dev/null && ufw delete allow 22/tcp >/dev/null 2>&1
        systemd-run --quiet --unit=copybot-ssh-revert --on-active=600 /usr/sbin/ufw allow 22/tcp >/dev/null 2>&1 || true
        echo "SSH now only from \$IP. Open a NEW SSH session to test it, then run: copybot ssh-allow keep"
        echo "(without 'keep' SSH opens to everyone again in 10 minutes; the Vultr web console always works)";;
   esac;;
 *) echo "usage: copybot status|start|stop|restart|logs|url|update|rollback|health|wallet|sethome|wallet-export|signer|perps-agent|perps-master|perps-guard|perps-set|ssh-allow";;
esac
CMD
  chmod +x /usr/local/bin/copybot.new && mv -f /usr/local/bin/copybot.new /usr/local/bin/copybot   # atomic: running shells keep the old inode
fi
cb_perms

echo "== restart + health check"
T=$(cb_now); cb_restart
if ! cb_health "$T" "${CB_HEALTH_TIMEOUT:-90}" 1; then rollback "bot not healthy within ${CB_HEALTH_TIMEOUT:-90}s (service down or no heartbeat)"; fi
trap - ERR
cb_status ok "$VERSION" "$FROM" "healthy"
echo "RESULT: ok"
echo "Updated ${FROM:0:7} -> ${VERSION:0:7}. PAPER MODE ONLY. Commands: copybot status|logs|url|update|rollback|health"
