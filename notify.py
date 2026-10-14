from tgfmt import term
from tgfmt import esc,plain,SEP,COMMANDS,kv
import os,json,logging,threading,urllib.error,time,urllib.request,urllib.parse
log=logging.getLogger("bot")
HEARTBEAT=os.environ.get("COPYBOT_HEARTBEAT") or os.path.join(os.path.dirname(os.path.abspath(__file__)),"heartbeat")
def heartbeat():
    """Touch the heartbeat file read by update.sh health check and the root watchdog (COPYBOT_HEARTBEAT)."""
    try: open(HEARTBEAT,"w").write(str(time.time()))
    except Exception as e: log.warning("heartbeat write failed: %s",e)
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
        log.info("NOTIFY: %s",plain(text).replace("\n"," | "))
        if not s.token: return
        c=s.chat_id()
        if not c: log.info("Telegram: no chat id yet - send /start to the bot");return
        try: s._api("sendMessage",{"chat_id":c,"text":text[:4000],"parse_mode":"HTML","disable_web_page_preview":True})
        except Exception as e:
            log.warning("Telegram HTML send failed (%s), retrying as plain text",e)
            try: s._api("sendMessage",{"chat_id":c,"text":plain(text)[:4000],"disable_web_page_preview":True})
            except Exception as e2: log.warning("Telegram send failed: %s",e2)


ALLOWED_KEYS=("BIRDEYE_API_KEY","JUPITER_API_KEY","NANSEN_API_KEY","HELIUS_API_KEY","FOMOAPI_KEY")
import re as _re
_VAL=_re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
def mask(v): return "…"+v[-4:] if v else "not set"
def keys_status():
    rows=[f"{k.replace('_API_KEY','').replace('_KEY',''):<9}: {mask(os.environ.get(k,'').strip())}" for k in ALLOWED_KEYS]
    return term("keys --masked",rows+[SEP,"set: /setkey NAME VALUE","(your message is wiped)"],("OK","api keys loaded"))
def _err(cmd,msg,hint=None): return term(cmd,[msg]+([SEP,hint] if hint else []),("ERR","failed"))
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
    def say(s,chat,text):
        """HTML reply; on any failure retry as plain text; never raises (listener must keep running)."""
        try: return s.api("sendMessage",{"chat_id":chat,"text":text[:4000],"parse_mode":"HTML","disable_web_page_preview":True},timeout=20)
        except Exception as e:
            log.warning("Telegram HTML reply failed (%s), retrying as plain text",e)
            try: return s.api("sendMessage",{"chat_id":chat,"text":plain(text)[:4000],"disable_web_page_preview":True},timeout=20)
            except Exception as e2: log.warning("Telegram reply failed: %s",e2)
    def setkey(s,db,chat,msg_id,text):
        """/setkey NAME VALUE -> delete the message first, validate, hand to root helper via stdin. Never logs the value."""
        try: s.api("deleteMessage",{"chat_id":chat,"message_id":msg_id},timeout=15)
        except Exception as e: log.warning("setkey: could not delete message (%s)",type(e).__name__)
        parts=text.split()
        if len(parts)!=3: return term("setkey",["usage: /setkey NAME VALUE",SEP,"allowed:"]+["  "+k for k in ALLOWED_KEYS],("WARN","bad syntax"))
        name,val=parts[1].upper(),parts[2]
        if name not in ALLOWED_KEYS:
            log.warning("setkey: rejected name %s",name[:40]);return term("setkey",["name not allowed",SEP,"allowed:"]+["  "+k for k in ALLOWED_KEYS],("ERR","permission denied"))
        if not _VAL.match(val): return _err("setkey","invalid value format","8-200 chars: A-Z a-z 0-9 . _ : -\nnothing saved")
        last=db.kv_get("last_setkey",0)
        if time.time()-last<20: return term("setkey",["wait a few seconds between","/setkey commands"],("WARN","rate limited"))
        db.kv_set("last_setkey",time.time())
        import subprocess
        try:
            r=subprocess.run(["sudo","-n","/usr/local/sbin/copybot-setkey"],input=f"{name}\n{val}\n",capture_output=True,text=True,timeout=30)
        except Exception as e:
            log.warning("setkey %s %s failed: %s",name,mask(val),type(e).__name__);return _err("setkey","helper not runnable","run `copybot update` once\non the server")
        if r.returncode!=0:
            log.warning("setkey %s %s failed: exit %s %s",name,mask(val),r.returncode,(r.stdout+r.stderr).strip()[-120:].replace(val,"***"))
            return _err("setkey","not saved: "+((r.stdout+r.stderr).strip().replace(val,"***")[-150:] or f"exit {r.returncode}"),"permission error? run\n`copybot update` on the server")
        log.info("setkey: %s saved (%s), restarting",name,mask(val))
        db.kv_set("pending_setkey",{"name":name,"masked":mask(val),"t":time.time()})
        return term("setkey "+name,[kv("key",name),kv("value",mask(val)),kv("action","restarting…")],("OK","🔑 key saved"))
    def run(s):
        if not s.token: log.info("Telegram commands disabled (no token)");return
        from db import DB
        db=DB(s.db_path)
        try: s.api("deleteWebhook",{"drop_pending_updates":False})
        except Exception as e: log.warning("Telegram deleteWebhook: %s",e)
        try: s.api("setMyCommands",{"commands":[{"command":c,"description":d} for c,d in COMMANDS]})
        except Exception as e: log.warning("Telegram setMyCommands: %s",e)
        log.info("Telegram command listener started")
        while True:
            heartbeat()
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
                        s.say(chat,out);continue
                    if cmd=="/keys":
                        s.say(chat,keys_status());continue
                    log.info("Telegram command %s",cmd)
                    try: out=s.handler(cmd,db)
                    except Exception as e: log.exception("command handler failed");out=_err(cmd.lstrip("/"),f"error building reply: {e}")
                    s.say(chat,out)
            except urllib.error.HTTPError as e:
                body=e.read().decode(errors="ignore")[:200]
                log.warning("Telegram HTTP %s: %s",e.code,body)
                time.sleep(30 if e.code==409 else 5)   # 409 = another process is polling this bot token
            except Exception as e:
                log.warning("Telegram listener error: %s",e);time.sleep(5)
