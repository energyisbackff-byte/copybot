#!/bin/bash
# Update /opt/copybot from this folder. Keeps .env, bot.db, dashboard token/url. Live trading stays LOCKED.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"; APP=/opt/copybot
[ -d $APP ] || { echo "not installed - run install.sh"; exit 1; }
echo "== updating code"
cp "$SRC"/*.py $APP/   # all modules (a fixed list missed tgfmt.py and crash-looped the bot)
for f in requirements.txt README.md install.sh update.sh; do cp "$SRC/$f" $APP/; done
for m in "$SRC"/*.py; do [ -f "$APP/$(basename "$m")" ] || { echo "ERROR: $(basename "$m") not installed"; exit 1; }; done
$APP/.venv/bin/python -m py_compile $APP/*.py || { echo "ERROR: code does not compile"; exit 1; }
mkdir -p $APP/dashboard $APP/systemd
cp "$SRC"/dashboard/{server.py,index.html,update_menu.py} $APP/dashboard/
cp "$SRC"/systemd/*.service $APP/systemd/; cp $APP/systemd/*.service /etc/systemd/system/
# config: keep user's file, but always force paper mode
[ -f $APP/config.yaml ] || cp "$SRC/config.yaml" $APP/
sed -i 's/^live_trading:.*/live_trading: false         # LOCKED/' $APP/config.yaml; rm -f $APP/LIVE_CONFIRM
$APP/.venv/bin/pip install -q -r $APP/requirements.txt
cp "$SRC/config.yaml" $APP/config.default.yaml
$APP/.venv/bin/python $APP/migrate_config.py $APP/config.yaml $APP/config.default.yaml
# version + self-update plumbing (root-owned script, narrow sudoers rule)
VERSION="${COPYBOT_VERSION:-$(curl -fsS -H "Accept: application/vnd.github.sha" https://api.github.com/repos/energyisbackff-byte/copybot/commits/main 2>/dev/null || echo unknown)}"
echo "$VERSION" > $APP/VERSION
install -o root -g root -m 0755 "$SRC/system/copybot-selfupdate" /usr/local/sbin/copybot-selfupdate
install -o root -g root -m 0440 "$SRC/system/copybot-update.sudoers" /tmp/copybot-update.sudoers.new
if visudo -cf /tmp/copybot-update.sudoers.new >/dev/null; then mv /tmp/copybot-update.sudoers.new /etc/sudoers.d/copybot-update; echo "sudoers rule installed (validated)"; else rm -f /tmp/copybot-update.sudoers.new; echo "WARNING: sudoers rule failed validation, /update disabled"; fi
install -o root -g root -m 0755 "$SRC/system/copybot-setkey" /usr/local/sbin/copybot-setkey
install -o root -g root -m 0440 "$SRC/system/copybot-setkey.sudoers" /tmp/copybot-setkey.sudoers.new
if visudo -cf /tmp/copybot-setkey.sudoers.new >/dev/null; then mv /tmp/copybot-setkey.sudoers.new /etc/sudoers.d/copybot-setkey; echo "setkey sudoers rule installed (validated)"; else rm -f /tmp/copybot-setkey.sudoers.new; echo "WARNING: setkey sudoers rule failed validation, /setkey disabled"; fi
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
 update) exec bash -c 'rm -rf /tmp/copybot-main && curl -fsSL https://github.com/energyisbackff-byte/copybot/archive/main.tar.gz | tar xz -C /tmp && exec bash /tmp/copybot-main/update.sh';;
 version) cat $APP/VERSION 2>/dev/null || echo unknown;;
 *) echo "usage: copybot status|start|stop|restart|logs|url|update";;
esac
CMD
chmod +x /usr/local/bin/copybot.new && mv -f /usr/local/bin/copybot.new /usr/local/bin/copybot   # atomic: running shells keep the old inode
chown -R copybot:copybot $APP; chown root:copybot $APP/.env; chmod 640 $APP/.env; chmod 600 $APP/dashboard/token.txt 2>/dev/null || true
systemctl daemon-reload; systemctl restart copybot-dashboard copybot
sleep 5; systemctl --no-pager status copybot copybot-dashboard | grep -E "●|Active:"
journalctl -u copybot -n 200 --no-pager | grep -E "command listener|Telegram" | tail -3 || true
echo "Updated. PAPER MODE ONLY. Try /status in Telegram. Commands: copybot status|logs|url|update"
