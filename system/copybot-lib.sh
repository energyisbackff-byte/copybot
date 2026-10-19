# Shared root-side helpers for update / rollback / watchdog. Independent of the bot's Python code.
# Paths are overridable (CB_*) only so the flow can be simulated in tests.
APP=${CB_APP:-/opt/copybot}; BK=${CB_BACKUPS:-/var/backups/copybot}; STATE=${CB_STATE:-/var/lib/copybot}
TG_API=${CB_TG_API:-https://api.telegram.org}; HB=$APP/heartbeat; STATUS=$STATE/update_status.json
LOCK=$STATE/update.lock; LIBDIR=${CB_LIBDIR:-/usr/local/lib/copybot}
mkdir -p "$BK" "$STATE"; chmod 755 "$STATE"; chmod 700 "$BK"
cb_now(){ date +%s; }
cb_gh_sha(){ # latest commit on main; uses GITHUB_TOKEN from .env when set (private repo), via a root-only header file
  local hf tok sha=""; hf=$(mktemp); chmod 600 "$hf"
  tok=$(sed -n 's/^GITHUB_TOKEN=//p' "$APP/.env" 2>/dev/null | tail -n 1 | tr -d "\"' \r" || true)
  if [ -n "$tok" ]; then printf 'Authorization: Bearer %s\n' "$tok" > "$hf"
    sha=$(curl -fsS -H @"$hf" -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/${CB_REPO:-energyisbackff-byte/copybot}/commits/main" 2>/dev/null || true); fi
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || sha=$(curl -fsS -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/${CB_REPO:-energyisbackff-byte/copybot}/commits/main" 2>/dev/null || true)
  rm -f "$hf"; [[ "$sha" =~ ^[0-9a-f]{40}$ ]] && echo "$sha" || echo unknown; }
cb_tg(){ # plain-text Telegram message straight to the Bot API; masks every .env value; never fails
  python3 - "$APP" "$TG_API" "$1" <<'PY' || true
import sys,os,json,re,sqlite3,urllib.request,urllib.parse
app,api,msg=sys.argv[1:4];env={}
msg=msg.encode("utf-8","surrogateescape").decode("utf-8","replace")  # C locale / byte-cut tails
try:
    for l in open(os.path.join(app,".env")):
        l=l.strip()
        if "=" in l and not l.startswith("#"):
            k,v=l.split("=",1);env[k.strip().removeprefix("export ").strip()]=v.strip().strip('"').strip("'")
except Exception: pass
tok=env.get("TELEGRAM_BOT_TOKEN","");chat=env.get("TELEGRAM_CHAT_ID","")
try:
    r=sqlite3.connect(f"file:{os.path.join(app,'bot.db')}?mode=ro",uri=True,timeout=5).execute("select v from kv where k='tg_chat_id'").fetchone()
    if r and json.loads(r[0]): chat=str(json.loads(r[0]))
except Exception: pass
for v in sorted(env.values(),key=len,reverse=True):
    if len(v)>=8: msg=msg.replace(v,"***")
msg=re.sub(r"\d{6,}:[A-Za-z0-9_-]{30,}","***",msg);msg=re.sub(r"(?i)(api[-_]?key=|bearer )[^&\s\"']+",r"\1***",msg)
if not tok or not chat: print("cb_tg: no token/chat id, skipped");sys.exit(0)
d=urllib.parse.urlencode({"chat_id":chat,"text":msg[:3900],"disable_web_page_preview":"true"}).encode()
try: urllib.request.urlopen(f"{api}/bot{tok}/sendMessage",d,timeout=20);print("cb_tg: sent")
except Exception as e: print("cb_tg: failed",type(e).__name__)
PY
}
cb_jtail(){ journalctl -u copybot -n "${1:-12}" --no-pager -o cat 2>/dev/null | cut -c1-160 | tail -c 1200; }
cb_mask(){ # stdin -> stdout with every .env value and token/key-looking strings replaced by ***
  python3 -c '
import sys,os,re
env={}
try:
    for l in open(os.path.join(sys.argv[1],".env")):
        l=l.strip()
        if "=" in l and not l.startswith("#"): env[l.split("=",1)[0]]=l.split("=",1)[1].strip().strip("\"").strip("\x27")
except Exception: pass
m=sys.stdin.buffer.read().decode("utf-8","replace")
for v in sorted(env.values(),key=len,reverse=True):
    if len(v)>=8: m=m.replace(v,"***")
m=re.sub(r"\d{6,}:[A-Za-z0-9_-]{30,}","***",m);m=re.sub(r"(?i)(api[-_]?key=|bearer )[^&\s\"\x27]+",r"\1***",m)
sys.stdout.buffer.write(m.encode())' "$APP" 2>/dev/null || echo "(log hidden)"; }
cb_status(){ # result version from detail   (detail is masked: file is world-readable for /health)
  local det; det=$(printf '%s' "${4:-}" | cb_mask)
  python3 - "$STATUS" "${1:-}" "${2:-}" "${3:-}" "$det" <<'PY' || true
import sys,json,time;p,res,ver,frm,det=sys.argv[1:6]
json.dump({"result":res,"version":ver,"from":frm,"detail":det.encode("utf-8","surrogateescape").decode("utf-8","replace")[-600:],"ts":time.time()},open(p,"w"))
PY
  chmod 644 "$STATUS" 2>/dev/null || true; }
cb_status_get(){ python3 -c "import json,sys;print(json.load(open('$STATUS')).get('$1',''))" 2>/dev/null || true; }
cb_snapshot(){ # code-only snapshot (no .env, db, logs, venv, dashboard secrets); keeps last 3
  local d="$BK/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$d"
  tar -C "$APP" --exclude=.env --exclude='bot.db*' --exclude='*.log' --exclude=.venv --exclude=heartbeat \
      --exclude=token.txt --exclude=url.txt --exclude=__pycache__ --exclude=STOP --exclude='*.pid' --exclude=memory -czf "$d/code.tgz" .
  (cat "$APP/VERSION" 2>/dev/null || echo unknown) > "$d/VERSION"
  ln -sfn "$d" "$BK/last"; ls -1d "$BK"/2* 2>/dev/null | head -n -3 | xargs -r rm -rf
  python3 -c "import json,time;json.dump({'version':open('$d/VERSION').read().strip(),'ts':time.time()},open('$STATE/last_snapshot.json','w'))"
  chmod 644 "$STATE/last_snapshot.json"; echo "$d"; }
cb_perms(){ id copybot >/dev/null 2>&1 || return 0
  chown -R copybot:copybot "$APP"; chown root:copybot "$APP/.env"; chmod 640 "$APP/.env"; chmod 600 "$APP/dashboard/token.txt" 2>/dev/null || true; }
cb_units(){ [ -n "${CB_TEST:-}" ] && return 0; cp "$APP"/systemd/*.service /etc/systemd/system/ 2>/dev/null || true; systemctl daemon-reload; }
cb_clear_pending(){ "$APP/.venv/bin/python" - "$APP/bot.db" <<'PY' || true
import sys,sqlite3;c=sqlite3.connect(sys.argv[1],timeout=10);c.execute("update kv set v='null' where k='pending_update'");c.commit()
PY
}
cb_restore(){ local d; d=$(readlink -f "$BK/last" 2>/dev/null) || true
  [ -n "$d" ] && [ -f "$d/code.tgz" ] || { echo "no snapshot to restore"; return 1; }
  tar -xzf "$d/code.tgz" -C "$APP" && cb_clear_pending && cb_perms && cb_units && echo "restored $(cat "$d/VERSION")"; }
cb_install_signer(){ # $1 = source dir. Separate user, root-owned code outside $APP, key in /var/lib/copybot-signer (700)
  local src=$1
  [ -n "${CB_TEST:-}" ] && return 0
  [ -f "$src/signer/signer.py" ] || return 1
  /usr/bin/python3 -c "import cryptography.hazmat.primitives.asymmetric.ed25519" 2>/dev/null || \
    { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-cryptography >/dev/null 2>&1 || return 1; }
  id copybot-signer >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d /var/lib/copybot-signer copybot-signer || return 1
  id -nG copybot 2>/dev/null | grep -qw copybot-signer || usermod -aG copybot-signer copybot || return 1
  install -d -o root -g root -m 0755 /usr/local/lib/copybot-signer || return 1
  local old new; old=$(cat /usr/local/lib/copybot-signer/signer.py /usr/local/lib/copybot-signer/solwire.py 2>/dev/null | sha256sum | cut -c1-16)
  install -o root -g root -m 0644 "$src/signer/solwire.py" "$src/signer/signer.py" /usr/local/lib/copybot-signer/ || return 1
  new=$(cat /usr/local/lib/copybot-signer/signer.py /usr/local/lib/copybot-signer/solwire.py | sha256sum | cut -c1-16)
  if [ "$old" != "$new" ]; then   # the boot message says so, so a signer change is never silent
    python3 -c "import json,time,sys;json.dump({'changed':True,'sha':sys.argv[1],'ts':time.time()},open(sys.argv[2],'w'))" "$new" "$STATE/signer_update.json"
    chmod 644 "$STATE/signer_update.json"; echo "signer code changed ($new)"; fi
  [ -f /etc/copybot-signer.json ] || install -o root -g root -m 0644 "$src/signer/limits.json" /etc/copybot-signer.json || return 1
  install -o root -g root -m 0644 "$src/signer/copybot-signer.service" /etc/systemd/system/copybot-signer.service || return 1
  systemctl daemon-reload && systemctl enable copybot-signer >/dev/null 2>&1 && systemctl restart copybot-signer || return 1
  sleep 2; systemctl is-active --quiet copybot-signer && echo "signer running" || return 1; }
cb_install_signer_spot(){ # $1 = source dir. The coin guard: same root-owned code as the signer, run in SPOT mode with its
  # OWN user, wallet (/var/lib/copybot-signer-spot, 700), socket and limits (/etc/copybot-signer-spot.json)
  local src=$1
  [ -n "${CB_TEST:-}" ] && return 0
  [ -f /usr/local/lib/copybot-signer/signer.py ] || return 1          # cb_install_signer runs first and installs the code
  id copybot-signer-spot >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d /var/lib/copybot-signer-spot copybot-signer-spot || return 1
  id -nG copybot 2>/dev/null | grep -qw copybot-signer-spot || usermod -aG copybot-signer-spot copybot || return 1
  [ -f /etc/copybot-signer-spot.json ] || install -o root -g root -m 0644 "$src/signer/limits-spot.json" /etc/copybot-signer-spot.json || return 1
  install -o root -g root -m 0644 "$src/signer/copybot-signer-spot.service" /etc/systemd/system/copybot-signer-spot.service || return 1
  systemctl daemon-reload && systemctl enable copybot-signer-spot >/dev/null 2>&1 && systemctl restart copybot-signer-spot || return 1
  sleep 2; systemctl is-active --quiet copybot-signer-spot && echo "coin guard running" || return 1; }
cb_install_perpsguard(){ # $1 = source dir. Like the signer: own user, root-owned code + venv, key in /var/lib/copybot-perps (700)
  local src=$1
  [ -n "${CB_TEST:-}" ] && return 0
  [ -f "$src/perpsguard/perpsguard.py" ] || return 1
  id copybot-perps >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d /var/lib/copybot-perps copybot-perps || return 1
  id -nG copybot 2>/dev/null | grep -qw copybot-perps || usermod -aG copybot-perps copybot || return 1
  install -d -o root -g root -m 0755 /usr/local/lib/copybot-perpsguard /opt/copybot-perpsguard || return 1
  [ -x /opt/copybot-perpsguard/venv/bin/python ] || /usr/bin/python3 -m venv /opt/copybot-perpsguard/venv || return 1
  if ! cmp -s "$src/perpsguard/requirements.txt" /opt/copybot-perpsguard/requirements.txt; then
    /opt/copybot-perpsguard/venv/bin/pip install -q -r "$src/perpsguard/requirements.txt" >/dev/null 2>&1 || return 1
    install -o root -g root -m 0644 "$src/perpsguard/requirements.txt" /opt/copybot-perpsguard/requirements.txt; fi
  install -o root -g root -m 0644 "$src/perpsguard/perpsguard.py" /usr/local/lib/copybot-perpsguard/ || return 1
  [ -f /etc/copybot-perpsguard.json ] || install -o root -g root -m 0644 "$src/perpsguard/limits.json" /etc/copybot-perpsguard.json || return 1
  install -o root -g root -m 0644 "$src/perpsguard/copybot-perpsguard.service" /etc/systemd/system/copybot-perpsguard.service || return 1
  systemctl daemon-reload && systemctl enable copybot-perpsguard >/dev/null 2>&1 && systemctl restart copybot-perpsguard || return 1
  sleep 2; systemctl is-active --quiet copybot-perpsguard && echo "perps guard running ($(python3 -c "import json;print(json.load(open('/etc/copybot-perpsguard.json')).get('network','test'))") net)" || return 1; }
cb_restart(){ systemctl restart copybot-dashboard copybot 2>/dev/null || systemctl restart copybot; }
cb_hb_age(){ [ -f "$HB" ] && echo $(( $(cb_now) - $(stat -c %Y "$HB") )) || echo -1; }
cb_health(){ # since timeout [require_heartbeat=1] -> 0 healthy
  local since=$1 to=$2 req=${3:-1} t0; t0=$(cb_now)
  while [ $(( $(cb_now) - t0 )) -lt "$to" ]; do
    if systemctl is-active --quiet copybot; then
      if [ "$req" = 0 ] || { [ -f "$HB" ] && [ "$(stat -c %Y "$HB")" -ge "$since" ]; }; then
        sleep "${CB_SETTLE:-8}"; systemctl is-active --quiet copybot && return 0
      fi
    fi
    sleep 3
  done; return 1; }
