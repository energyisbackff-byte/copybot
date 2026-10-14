import os,json,logging,urllib.request,urllib.parse
log=logging.getLogger("bot")
class Notifier:
    def __init__(s,db,enabled=True):
        s.db=db;s.token=os.environ.get("TELEGRAM_BOT_TOKEN","").strip() if enabled else ""
        if not s.token: log.info("Telegram: no TELEGRAM_BOT_TOKEN, notifier will log only")
    def _api(s,m,params):
        u=f"https://api.telegram.org/bot{s.token}/{m}?"+urllib.parse.urlencode(params)
        return json.load(urllib.request.urlopen(u,timeout=20))
    def chat_id(s):
        c=s.db.kv_get("tg_chat_id")
        if c or not s.token: return c
        try:
            for u in reversed(s._api("getUpdates",{}).get("result",[])):
                m=u.get("message") or {}
                if m.get("chat",{}).get("id"):
                    c=str(m["chat"]["id"]);s.db.kv_set("tg_chat_id",c);log.info("Telegram chat id discovered");return c
        except Exception as e: log.warning("Telegram getUpdates failed: %s",e)
        return None
    def send(s,text):
        log.info("NOTIFY: %s",text.replace("\n"," | "))
        if not s.token: return
        c=s.chat_id()
        if not c: log.info("Telegram: open your bot and tap Start so I can find your chat id");return
        try: s._api("sendMessage",{"chat_id":c,"text":text[:4000]})
        except Exception as e: log.warning("Telegram send failed: %s",e)

    def commands(s,handler):
        """Poll getUpdates (offset-tracked) for /status and /dashboard from the saved chat only."""
        c=s.db.kv_get("tg_chat_id")
        if not s.token or not c: return
        try: ups=s._api("getUpdates",{"offset":s.db.kv_get("tg_offset",0),"timeout":0}).get("result",[])
        except Exception as e: log.debug("getUpdates %s",e);return
        for u in ups:
            s.db.kv_set("tg_offset",u["update_id"]+1);m=u.get("message") or {}
            if str(m.get("chat",{}).get("id"))!=str(c):continue
            cmd=(m.get("text") or "").split("@")[0].strip().lower()
            if cmd in("/status","/dashboard","/start","/help"):
                try:s._api("sendMessage",{"chat_id":c,"text":handler(cmd)[:4000]})
                except Exception as e:log.warning("reply failed %s",e)
