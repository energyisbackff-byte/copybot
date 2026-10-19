#!/bin/bash
# One-shot installer: paper copy bot + HTTPS dashboard. The old copy-trading stub stays off; real money is the /live feature (available, off until you switch it on).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"; APP=/opt/copybot
# Telegram chat id: from the environment, else an existing .env, else asked once (blank = bind on first /start)
CHAT="${TELEGRAM_CHAT_ID:-}"
[ -z "$CHAT" ] && [ -s $APP/.env ] && CHAT=$(sed -n 's/^TELEGRAM_CHAT_ID=//p' $APP/.env | head -1)
if [ -z "$CHAT" ] && [ -r /dev/tty ]; then read -rp "Your Telegram chat id (digits, Enter to skip and bind on first /start): " CHAT </dev/tty || true; fi
[[ "$CHAT" =~ ^-?[0-9]{5,20}$ ]] || CHAT=""
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
sed -i 's/^live_trading:.*/live_trading: false         # old copy-trading stub, unused (real money = \/live)/' $APP/config.yaml
chown -R copybot:copybot $APP; chown root:copybot $APP/.env; chmod 640 $APP/.env
# save chat id in bot db
[ -n "$CHAT" ] && sudo -u copybot $APP/.venv/bin/python -c "import sys;sys.path.insert(0,'$APP');from db import DB;DB('$APP/bot.db').kv_set('tg_chat_id','$CHAT')" || echo "(no chat id given: send /start to your bot to link it)"
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
echo "== root helpers, services, console command (frozen: later changed only with 'copybot system-update')"
( . "$SRC/system/copybot-lib.sh"; cb_install_system "$SRC" )
echo "== live wallet signer (live trading stays OFF until you arm it)"
( . "$SRC/system/copybot-lib.sh"; cb_install_signer "$SRC" ) || echo "WARNING: signer not installed; paper trading unaffected"
( . "$SRC/system/copybot-lib.sh"; cb_install_signer_spot "$SRC" ) || echo "WARNING: coin guard not installed; paper trading unaffected"
echo "== perps guard (real-money perps stay OFF until you switch them on; testnet by default)"
( . "$SRC/system/copybot-lib.sh"; cb_install_perpsguard "$SRC" ) || echo "WARNING: perps guard not installed; paper perps unaffected"
(curl -fsS -H "Accept: application/vnd.github.sha" https://api.github.com/repos/energyisbackff-byte/copybot/commits/main || echo unknown) > $APP/VERSION
systemctl enable --now copybot-dashboard copybot >/dev/null
sleep 8
set -a; . $APP/.env; set +a; python3 $APP/dashboard/update_menu.py || echo "(menu button not set - check TELEGRAM_BOT_TOKEN)"
echo; systemctl --no-pager -l status copybot copybot-dashboard caddy | grep -E "●|Active:"
echo; echo "Copy trading is paper only. Real money: send /live in Telegram (available, off until you switch it on)."
echo "Dashboard: https://$HOST/?t=$(cat $APP/dashboard/token.txt)"
echo "(HTTPS certificate can take ~1 minute on first visit.) Commands: copybot status | logs | url | stop | start"
