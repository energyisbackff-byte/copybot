import os,json,logging,threading,urllib.error,time,urllib.request,urllib.parse
log=logging.getLogger("bot")
class Notifier:
    def __init__(s,db,enabled=True):
        s.db=db;s.token=os.environ.get("TELEGRAM_BOT_TOKEN","").strip() if enabled else ""
        if not s.token: log.info("Telegram: no TELEGRAM_BOT_TOKEN, notifier will log only")
    def _api(s,m,params):
        u=f"https://api.telegram.org/bot{s.token}/{m}?"+urllib.parse.urlencode(params)
        return json.load(urllib.request.urlopen(u,timeout=20))
    def chat_id(s):
        return s.db.kv_get("tg_chat_id") or os.environ.get("TELEGRAM_CHAT_ID")   # discovery is done by CommandListener on /start
    def send(s,text):
        log.info("NOTIFY: %s",text.replace("\n"," | "))
        if not s.token: return
        c=s.chat_id()
        if not c: log.info("Telegram: no chat id yet - send /start to the bot");return
        try: s._api("sendMessage",{"chat_id":c,"text":text[:4000]})
        except Exception as e: log.warning("Telegram send failed: %s",e)


ALLOWED_KEYS=("BIRDEYE_API_KEY","JUPITER_API_KEY","NANSEN_API_KEY","HELIUS_API_KEY","FOMOAPI_KEY")
import re as _re
_VAL=_re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
def mask(v): return "…"+v[-4:] if v else "not set"
def keys_status():
    return "🔑 API keys (masked):\n"+"\n".join(f"• {k}: {mask(os.environ.get(k,'').strip())}" for k in ALLOWED_KEYS)+"\nSet one with: /setkey NAME VALUE (your message is deleted)"
class CommandListener(threading.Thread):
    """Answers /start /status /dashboard /help in its own thread (long polling), so slow trader
    selection or wallet polling in the main loop can never block replies. Uses its own DB connection."""
    def __init__(s,db_path,handler):
        super().__init__(daemon=True);s.db_path=db_path;s.handler=handler
        s.token=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
    def api(s,m,params,timeout=40):
        u=f"https://api.telegram.org/bot{s.token}/{m}"
        req=urllib.request.Request(u,json.dumps(params).encode(),{"content-type":"application/json"})
        return json.load(urllib.request.urlopen(req,timeout=timeout))
    def setkey(s,db,chat,msg_id,text):
        """/setkey NAME VALUE -> delete the message first, validate, hand to root helper via stdin. Never logs the value."""
        try: s.api("deleteMessage",{"chat_id":chat,"message_id":msg_id},timeout=15)
        except Exception as e: log.warning("setkey: could not delete message (%s)",type(e).__name__)
        parts=text.split()
        if len(parts)!=3: return "Usage: /setkey NAME VALUE\nAllowed: "+", ".join(ALLOWED_KEYS)
        name,val=parts[1].upper(),parts[2]
        if name not in ALLOWED_KEYS:
            log.warning("setkey: rejected name %s",name[:40]);return "❌ Not allowed. Allowed names: "+", ".join(ALLOWED_KEYS)
        if not _VAL.match(val): return "❌ Invalid value format (8-200 chars: letters, digits, . _ : -). Nothing saved."
        last=db.kv_get("last_setkey",0)
        if time.time()-last<20: return "⏳ Please wait a few seconds between /setkey commands."
        db.kv_set("last_setkey",time.time())
        import subprocess
        try:
            r=subprocess.run(["sudo","-n","/usr/local/sbin/copybot-setkey"],input=f"{name}\n{val}\n",capture_output=True,text=True,timeout=30)
        except Exception as e:
            log.warning("setkey %s %s failed: %s",name,mask(val),type(e).__name__);return "❌ Could not save the key (helper not runnable). Run `copybot update` once on the server."
        if r.returncode!=0:
            log.warning("setkey %s %s failed: exit %s %s",name,mask(val),r.returncode,(r.stdout+r.stderr).strip()[-120:].replace(val,"***"))
            return "❌ Not saved: "+((r.stdout+r.stderr).strip().replace(val,"***")[-150:] or f"exit {r.returncode}")+"\n(if this is a permission error, run `copybot update` once on the server)"
        log.info("setkey: %s saved (%s), restarting",name,mask(val))
        db.kv_set("pending_setkey",{"name":name,"masked":mask(val),"t":time.time()})
        return f"🔑 {name} saved ({mask(val)}). Restarting…"
    def run(s):
        if not s.token: log.info("Telegram commands disabled (no token)");return
        from db import DB
        db=DB(s.db_path)
        try: s.api("deleteWebhook",{"drop_pending_updates":False})
        except Exception as e: log.warning("Telegram deleteWebhook: %s",e)
        try: s.api("setMyCommands",{"commands":[{"command":"status","description":"balance, positions, traders"},{"command":"dashboard","description":"open the dashboard"},{"command":"version","description":"running commit"},{"command":"update","description":"pull latest code from GitHub and restart"},{"command":"keys","description":"which API keys are set (masked)"},{"command":"setkey","description":"/setkey NAME VALUE (message is deleted)"},{"command":"help","description":"what this bot does"}]})
        except Exception as e: log.warning("Telegram setMyCommands: %s",e)
        log.info("Telegram command listener started")
        while True:
            try:
                r=s.api("getUpdates",{"offset":db.kv_get("tg_offset",0),"timeout":25,"allowed_updates":["message"]},timeout=40)
                if not r.get("ok"): log.warning("getUpdates not ok: %s",r);time.sleep(5);continue
                for u in r.get("result",[]):
                    db.kv_set("tg_offset",u["update_id"]+1)
                    m=u.get("message") or {};chat=str((m.get("chat") or {}).get("id",""));text=(m.get("text") or "").strip()
                    if not text.startswith("/"):continue
                    saved=str(db.kv_get("tg_chat_id") or "")
                    if not saved and text.lower().startswith("/start"): db.kv_set("tg_chat_id",chat);saved=chat
                    if chat!=saved: log.warning("Telegram: ignoring command from unknown chat %s",chat);continue
                    cmd=text.split()[0].split("@")[0].lower()
                    if cmd=="/setkey":
                        out=s.setkey(db,chat,m.get("message_id"),text)
                        s.api("sendMessage",{"chat_id":chat,"text":out},timeout=20);continue
                    if cmd=="/keys":
                        s.api("sendMessage",{"chat_id":chat,"text":keys_status()},timeout=20);continue
                    log.info("Telegram command %s",cmd)
                    try: out=s.handler(cmd,db)
                    except Exception as e: log.exception("command handler failed");out=f"error building reply: {e}"
                    s.api("sendMessage",{"chat_id":chat,"text":out[:4000],"disable_web_page_preview":True},timeout=20)
            except urllib.error.HTTPError as e:
                body=e.read().decode(errors="ignore")[:200]
                log.warning("Telegram HTTP %s: %s",e.code,body)
                time.sleep(30 if e.code==409 else 5)   # 409 = another process is polling this bot token
            except Exception as e:
                log.warning("Telegram listener error: %s",e);time.sleep(5)
