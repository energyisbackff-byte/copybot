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
    def run(s):
        if not s.token: log.info("Telegram commands disabled (no token)");return
        from db import DB
        db=DB(s.db_path)
        try: s.api("deleteWebhook",{"drop_pending_updates":False})
        except Exception as e: log.warning("Telegram deleteWebhook: %s",e)
        try: s.api("setMyCommands",{"commands":[{"command":"status","description":"balance, positions, traders"},{"command":"dashboard","description":"open the dashboard"},{"command":"version","description":"running commit"},{"command":"update","description":"pull latest code from GitHub and restart"},{"command":"help","description":"what this bot does"}]})
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
