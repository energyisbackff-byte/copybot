#!/bin/bash
# One-shot installer: paper copy bot + HTTPS dashboard. Live trading stays LOCKED.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"; APP=/opt/copybot; CHAT=8815474393
IP=$(curl -4 -fsS https://api.ipify.org || hostname -I | awk '{print $1}'); HOST="${IP//./-}.sslip.io"
echo "== installing packages"; export DEBIAN_FRONTEND=noninteractive
apt-get update -qq; apt-get install -y -qq python3 python3-venv python3-pip curl ufw debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq; apt-get install -y -qq caddy >/dev/null; fi
id copybot >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d $APP copybot
mkdir -p $APP; cp -r "$SRC"/. $APP/; rm -f $APP/LIVE_CONFIRM
python3 -m venv $APP/.venv; $APP/.venv/bin/pip install -q -r $APP/requirements.txt
if [ ! -s $APP/.env ]; then
  echo "== paste your keys (input hidden, press Enter after each)"
  read -rsp "HELIUS_API_KEY: " H </dev/tty; echo; read -rsp "FOMOAPI_KEY: " F </dev/tty; echo; read -rsp "TELEGRAM_BOT_TOKEN: " T </dev/tty; echo
  umask 077; printf 'HELIUS_API_KEY=%s\nFOMOAPI_KEY=%s\nTELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' "$H" "$F" "$T" "$CHAT" > $APP/.env; umask 022
fi
chmod 600 $APP/.env
[ -s $APP/dashboard/token.txt ] || python3 -c "import secrets;print(secrets.token_urlsafe(32))" > $APP/dashboard/token.txt
chmod 600 $APP/dashboard/token.txt; echo "https://$HOST" > $APP/dashboard/url.txt
# force paper mode
sed -i 's/^live_trading:.*/live_trading: false         # LOCKED/' $APP/config.yaml
chown -R copybot:copybot $APP; chown root:copybot $APP/.env; chmod 640 $APP/.env
# save chat id in bot db
sudo -u copybot $APP/.venv/bin/python -c "import sys;sys.path.insert(0,'$APP');from db import DB;DB('$APP/bot.db').kv_set('tg_chat_id','$CHAT')"
echo "== firewall"; ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null
echo "== HTTPS ($HOST)"
cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
  encode gzip
  header Referrer-Policy no-referrer
  reverse_proxy 127.0.0.1:8787
}
CADDY
systemctl restart caddy
cp $APP/systemd/*.service /etc/systemd/system/; systemctl daemon-reload
install -o root -g root -m 0755 $APP/system/copybot-selfupdate /usr/local/sbin/copybot-selfupdate
install -o root -g root -m 0440 $APP/system/copybot-update.sudoers /tmp/cbs.new && visudo -cf /tmp/cbs.new >/dev/null && mv /tmp/cbs.new /etc/sudoers.d/copybot-update
install -o root -g root -m 0755 $APP/system/copybot-setkey /usr/local/sbin/copybot-setkey
install -o root -g root -m 0440 $APP/system/copybot-setkey.sudoers /tmp/cbk.new && visudo -cf /tmp/cbk.new >/dev/null && mv /tmp/cbk.new /etc/sudoers.d/copybot-setkey
(curl -fsS -H "Accept: application/vnd.github.sha" https://api.github.com/repos/energyisbackff-byte/copybot/commits/main || echo unknown) > $APP/VERSION
systemctl enable --now copybot-dashboard copybot >/dev/null
sleep 8
set -a; . $APP/.env; set +a; python3 $APP/dashboard/update_menu.py || echo "(menu button not set - check TELEGRAM_BOT_TOKEN)"
cat > /usr/local/bin/copybot.new <<CMD
#!/bin/bash
case "\$1" in
 status) cd $APP && sudo -u copybot $APP/.venv/bin/python main.py status;;
 stop) systemctl stop copybot; echo stopped;;
 start) systemctl start copybot; echo started;;
 logs) journalctl -u copybot -n 50 --no-pager;;
 url) echo "https://$HOST/?t=\$(cat $APP/dashboard/token.txt)";;
 restart) systemctl restart copybot copybot-dashboard; echo restarted;;
 update) exec bash -c 'rm -rf /tmp/copybot-main && curl -fsSL https://github.com/energyisbackff-byte/copybot/archive/main.tar.gz | tar xz -C /tmp && exec bash /tmp/copybot-main/update.sh';;
 *) echo "usage: copybot status|start|stop|restart|logs|url|update";;
esac
CMD
chmod +x /usr/local/bin/copybot.new && mv -f /usr/local/bin/copybot.new /usr/local/bin/copybot   # atomic: running shells keep the old inode
echo; systemctl --no-pager -l status copybot copybot-dashboard caddy | grep -E "●|Active:"
echo; echo "PAPER MODE ONLY - live trading locked."
echo "Dashboard: https://$HOST/?t=$(cat $APP/dashboard/token.txt)"
echo "(HTTPS certificate can take ~1 minute on first visit.) Commands: copybot status | logs | url | stop | start"
