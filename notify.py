from tgfmt import term
from tgfmt import esc,plain,SEP,COMMANDS,kv,wrap
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
    def _post(s,c,text,extra):
        try: s._api("sendMessage",dict({"chat_id":c,"text":text[:4000],"parse_mode":"HTML","disable_web_page_preview":True},**extra));return True
        except Exception as e:
            log.warning("Telegram HTML send failed (%s), retrying as plain text",e)
            try: s._api("sendMessage",{"chat_id":c,"text":plain(text)[:4000],"disable_web_page_preview":True});return True
            except Exception as e2: log.warning("Telegram send failed: %s",e2);return False
    def send(s,text,buttons=None,tier="instant",digest=None):
        """Alert tiers: 'instant' (fills, errors, approvals) goes out now, to the alerts chat when one is set
        (TELEGRAM_ALERTS_CHAT_ID, your lock-screen channel) else to the bot chat; 'critical' (kill switches, panic,
        mismatches) goes to both and is marked 🚨; 'digest' (routine refusals) is queued and sent once an hour as one
        message. buttons: [[(label, callback_data), ...], ...] -> inline keyboard (live-trade approvals)."""
        log.info("NOTIFY[%s]: %s",tier,plain(text).replace("\n"," | "))
        try:
            d=time.strftime("%Y-%m-%d",time.gmtime());cnt=s.db.kv_get("notify:count") or {}
            cnt={k:v for k,v in cnt.items() if k>=time.strftime("%Y-%m-%d",time.gmtime(time.time()-7*86400))};cnt[d]=cnt.get(d,0)+(tier!="digest")
            s.db.kv_set("notify:count",cnt)
        except Exception:pass
        if tier=="digest":
            try:
                q=s.db.kv_get("notify:digest") or [];q.append([time.time(),digest or plain(text).replace("\n"," ")[:160]]);s.db.kv_set("notify:digest",q[-300:])
            except Exception as e:log.warning("digest queue: %s",e)
            return
        if not s.token: return
        c=s.chat_id()
        if not c: log.info("Telegram: no chat id yet - send /start to the bot");return
        extra={"reply_markup":json.dumps({"inline_keyboard":[[{"text":t,"callback_data":d} for t,d in row] for row in buttons]})} if buttons else {}
        al=os.environ.get("TELEGRAM_ALERTS_CHAT_ID","").strip()
        if tier=="critical":
            text="🚨 "+text
            if al and al!=str(c):s._post(al,text,{})
            s._post(c,text,extra);return
        if al and not buttons:
            if s._post(al,text,extra):return
        s._post(c,text,extra)
    def flush_digest(s,every_s=3600,force=False):
        """Send the queued routine messages as one hourly digest. Returns the number of lines sent."""
        q=s.db.kv_get("notify:digest") or []
        if not q or (not force and time.time()-q[0][0]<every_s):return 0
        s.db.kv_set("notify:digest",[])
        by={}
        for _,line in q:by[line]=by.get(line,0)+1
        rows=[(f"{n}x " if n>1 else "")+line for line,n in sorted(by.items(),key=lambda x:-x[1])]
        body=sum((wrap(r,"- ","  ") for r in rows[:40]),[])+([f"... and {len(rows)-40} more"] if len(rows)>40 else [])
        s.send(term("digest --hourly",body+[SEP,"routine messages, grouped.","fills, errors and approvals","always arrive instantly."],("OK",f"🗂 {len(q)} routine message(s)")),tier="instant")
        return len(q)

def send_document(token,chat,filename,data,caption=None):
    """Send a file to the chat (multipart upload). Never raises; returns True when Telegram accepted it."""
    if not token or not chat:return False
    b="----zetrax"+str(int(time.time()*1000));parts=[]
    for k,v in(("chat_id",str(chat)),)+((("caption",caption[:1000]),) if caption else ()):
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\nContent-Type: text/csv\r\n\r\n".encode()+data+b"\r\n")
    body=b"".join(parts)+f"--{b}--\r\n".encode()
    try:
        r=urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument",body,{"content-type":f"multipart/form-data; boundary={b}"})
        return bool(json.load(urllib.request.urlopen(r,timeout=60)).get("ok"))
    except Exception as e:log.warning("Telegram sendDocument failed: %s",e);return False
ALLOWED_KEYS=("BIRDEYE_API_KEY","JUPITER_API_KEY","NANSEN_API_KEY","HELIUS_API_KEY","FOMOAPI_KEY","GITHUB_TOKEN","X_BEARER_TOKEN","TELEGRAM_ALERTS_CHAT_ID")
import re as _re
_VAL=_re.compile(r"^[A-Za-z0-9._:%=+/-]{8,400}$")
def mask(v): return "…"+v[-4:] if v else "not set"
def keys_status():
    rows=[f"{k.replace('_API_KEY','').replace('_KEY','').replace('_TOKEN',''):<9}: {mask(os.environ.get(k,'').strip())}" for k in ALLOWED_KEYS]
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
        if not _VAL.match(val): return _err("setkey","invalid value format","8-400 chars: A-Z a-z 0-9 . _ : % = + / -\nnothing saved")
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
    def on_callback(s,db,cq):
        """Approve / Reject taps on a live-trade approval. Only the saved chat counts; only the current request id."""
        chat=str(((cq.get("message") or {}).get("chat") or {}).get("id",""));data=cq.get("data") or ""
        ans=lambda t:s.api("answerCallbackQuery",{"callback_query_id":cq["id"],"text":t},timeout=15)
        try:
            if chat!=str(db.kv_get("tg_chat_id") or ""):return ans("not allowed")
            p=data.split(":")
            if len(p)!=3 or p[0] not in("appr","pappr","sappr"):return ans("unknown button")
            if p[0]=="sappr":     # real money for the coin books (spot_live.py): its own approval
                a=db.kv_get("spot-live:approval")
                if not a or a.get("id")!=p[1] or a.get("status")!="pending":return ans("this request is no longer open")
                if time.time()>a.get("exp",0):return ans("too late, it expired")
                a["status"]="yes" if p[2]=="yes" else "no";db.kv_set("spot-live:approval",a);log.info("spot live approval %s: %s",p[1],a["status"])
                ans("approved, buying now" if a["status"]=="yes" else "rejected, skipped")
                return s.say(chat,term("real --approve",["you approved: buying now" if a["status"]=="yes" else "you rejected: skipped"],("OK","answer received")))
            if p[0]=="pappr":     # real-money perps (perps_live.py): its own approval, never the Trend Rider one
                a=db.kv_get("perps-live:approval")
                if not a or a.get("id")!=p[1] or a.get("status")!="pending":return ans("this request is no longer open")
                if time.time()>a.get("exp",0):return ans("too late, it expired")
                a["status"]="yes" if p[2]=="yes" else "no";db.kv_set("perps-live:approval",a);log.info("perps live approval %s: %s",p[1],a["status"])
                ans("approved, ordering now" if a["status"]=="yes" else "rejected, skipped")
                return s.say(chat,term("perps live --approve",["you approved: ordering now" if a["status"]=="yes" else "you rejected: skipped"],("OK","answer received")))
            a=db.kv_get("live:approval")
            if not a or a.get("id")!=p[1] or a.get("status")!="pending":return ans("this request is no longer open")
            if time.time()>a.get("exp",0):return ans("too late, it expired")
            a["status"]="yes" if p[2]=="yes" else "no";db.kv_set("live:approval",a);log.info("live approval %s: %s",p[1],a["status"])
            ans("approved, buying now" if a["status"]=="yes" else "rejected, skipped")
            s.say(chat,term("live --approve",["you approved: buying now" if a["status"]=="yes" else "you rejected: skipped"],("OK","answer received")))
        except Exception as e:log.warning("callback: %s",e)
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
                r=s.api("getUpdates",{"offset":db.kv_get("tg_offset",0),"timeout":25,"allowed_updates":["message","callback_query"]},timeout=40)
                if not r.get("ok"): log.warning("getUpdates not ok: %s",r);time.sleep(5);continue
                for u in r.get("result",[]):
                    db.kv_set("tg_offset",u["update_id"]+1)
                    if u.get("callback_query"):
                        s.on_callback(db,u["callback_query"]);continue
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
                    try: out=s.handler(cmd,db,text)
                    except Exception as e: log.exception("command handler failed");out=_err(cmd.lstrip("/"),f"error building reply: {e}")
                    s.say(chat,out)
            except urllib.error.HTTPError as e:
                body=e.read().decode(errors="ignore")[:200]
                log.warning("Telegram HTTP %s: %s",e.code,body)
                time.sleep(30 if e.code==409 else 5)   # 409 = another process is polling this bot token
            except Exception as e:
                log.warning("Telegram listener error: %s",e);time.sleep(5)
