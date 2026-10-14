#!/usr/bin/env python3
"""Paper copy-trading bot. Usage: main.py run | status | summary | select"""
import os,sys,time,json,queue,logging,yaml,datetime as dt
HERE=os.path.dirname(os.path.abspath(__file__));os.chdir(HERE);sys.path.insert(0,HERE)
from db import DB;from chain import Helius,Fomo,classify,parse_tx,sol_usd;from notify import Notifier,CommandListener;from paper import Paper,exec_stats,PAPER_BOOKS;from selection import select;from wsmon import WalletStream
from notify import heartbeat,HEARTBEAT
from tgfmt import term,kv,usd,esc,SEP,PAPER,COMMANDS
import execution  # disabled stub, never called in paper mode
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s",handlers=[logging.FileHandler("bot.log"),logging.StreamHandler()])
log=logging.getLogger("bot");cfg=yaml.safe_load(open("config.yaml"));db=DB(os.environ.get("COPYBOT_DB","bot.db"))
assert cfg.get("mode")=="paper","only paper mode supported"
NO_TG=os.environ.get("COPYBOT_NO_TELEGRAM")=="1"
def version():
    try:return open(os.path.join(HERE,"VERSION")).read().strip() or "unknown"
    except Exception:return "unknown"
def test_key(name):
    """Cheap live check that a key loaded and works. Never prints the key."""
    import urllib.request,urllib.error
    k=os.environ.get(name,"").strip()
    if not k:return False,"not present in environment"
    try:
        if name=="BIRDEYE_API_KEY":
            r=json.load(urllib.request.urlopen(urllib.request.Request("https://public-api.birdeye.so/defi/price?address=So11111111111111111111111111111111111111112",headers={"X-API-KEY":k,"x-chain":"solana","accept":"application/json"}),timeout=15))
            return bool(r.get("success")),"Birdeye price call "+("worked" if r.get("success") else "returned success=false")
        if name=="JUPITER_API_KEY":
            urllib.request.urlopen(urllib.request.Request("https://api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112",headers={"x-api-key":k,"user-agent":"copybot"}),timeout=15);return True,"Jupiter price call worked"
        if name=="HELIUS_API_KEY":
            r=json.load(urllib.request.urlopen(urllib.request.Request("https://mainnet.helius-rpc.com/?api-key="+k,json.dumps({"jsonrpc":"2.0","id":1,"method":"getSlot"}).encode(),{"content-type":"application/json"}),timeout=15))
            return "result" in r,"Helius getSlot "+("worked" if "result" in r else "failed")
        if name=="GITHUB_TOKEN":
            r=json.load(urllib.request.urlopen(urllib.request.Request("https://api.github.com/repos/energyisbackff-byte/copybot",headers={"authorization":"Bearer "+k,"accept":"application/vnd.github+json","user-agent":"copybot"}),timeout=15))
            return True,"GitHub can read the repo ("+("private" if r.get("private") else "public")+"), /update will use it"
        if name=="FOMOAPI_KEY":
            urllib.request.urlopen(urllib.request.Request("https://api.fomoapi.io/v2/leaderboard/24h?limit=1",headers={"authorization":"Bearer "+k}),timeout=20);return True,"fomoapi leaderboard call worked (250 credits)"
        return True,"loaded (no free test call for this provider)"
    except urllib.error.HTTPError as e: return False,f"HTTP {e.code} from provider (key rejected or plan limit)"
    except Exception as e: return False,type(e).__name__
STATE=os.environ.get("CB_STATE","/var/lib/copybot")
def _age(sec):
    if sec is None or sec<0:return "–"
    sec=int(sec);return f"{sec}s" if sec<120 else f"{sec//60}m" if sec<7200 else f"{sec//3600}h{sec%3600//60:02d}m" if sec<172800 else f"{sec//86400}d"
def _jload(p):
    try:return json.load(open(p))
    except Exception:return {}
def health_text(plain=False):
    now=time.time();pidf=os.environ.get("COPYBOT_PID","bot.pid")
    up=now-os.path.getmtime(pidf) if os.path.exists(pidf) else None
    hb=now-os.path.getmtime(HEARTBEAT) if os.path.exists(HEARTBEAT) else None
    u=_jload(os.path.join(STATE,"update_status.json"));sn=_jload(os.path.join(STATE,"last_snapshot.json"))
    wd=os.path.join(STATE,"watchdog","bad_since");wbad=os.path.exists(wd)
    ok=hb is not None and hb<300 and not wbad
    res=u.get("result","–");tag={"ok":"OK","rolled_back":"WARN","preflight_failed":"WARN"}.get(res,"ERR" if res!="–" else "OK")
    rows=[kv("mode",PAPER),kv("version",version()[:7]),kv("uptime",_age(up)),kv("heartbt",_age(hb)+(" ago" if hb is not None else "")),
        kv("watchdog","🔴 alerting" if wbad else "🟢 ok"),SEP,"last update:",kv("  result",f"[{tag}] {res}"),
        kv("  to",str(u.get("version","–"))[:7]),kv("  from",str(u.get("from","–"))[:7]),kv("  when",_age(now-u["ts"])+" ago" if u.get("ts") else "–")]
    if u.get("detail") and res!="ok": rows.append(kv("  detail",str(u["detail"])[:160]))
    rows+=[SEP,kv("snapshot",(str(sn.get("version","–"))[:7]+(" ("+_age(now-sn["ts"])+" ago)" if sn.get("ts") else "")) if sn else "none yet")]
    t=term("health",rows,("OK","🟢 healthy") if ok else ("WARN","degraded"))
    if plain:
        from tgfmt import plain as _p;return _p(t)
    return t
def exits_text():
    e=cfg.get("exits") or {}
    if not e.get("enabled"):return "off (trader sells only)"
    parts=[f"SL -{e['stop_loss_pct']:.0%}" if e.get("stop_loss_pct") else None,f"TP +{e['take_profit_pct']:.0%}" if e.get("take_profit_pct") else None,
           f"{e['max_hold_hours']:g}h" if e.get("max_hold_hours") else None]
    return " ".join(p for p in parts if p) or "on"
def stop_requested(): return os.path.exists(cfg["safeguards"]["stop_file"])
def book_enabled(b):return ((cfg.get("books") or {}).get(b) or {}).get("enabled",b=="copy")
def books_text(cdb):
    rows=[]
    for b in PAPER_BOOKS:
        if not book_enabled(b) and not cdb.kv_get(b+":cash"):continue
        cash=cdb.kv_get(b+":cash",0) or 0;eq=cdb.kv_get(b+":last_equity",cash) or cash
        start=((cfg.get("books") or {}).get(b) or {}).get("start_equity_usd") or cfg["paper"]["start_equity_usd"]
        op=cdb.q("select count(*) from b_positions where book=? and status='open'",(b,))[0][0]
        state="off" if not book_enabled(b) else "PAUSED" if cdb.kv_get(b+":paused") else "SHUTDOWN" if cdb.kv_get(b+":shutdown") else "ok"
        rows+=[f"{b:<10} paper  {usd(eq):>10}",f"  {usd(eq-start,True)} · {op} open · {state}"]
    if cdb.kv_get("panic"):rows+=[SEP,"[ERR] PANIC STOP active"]
    return term("books --list",rows+[SEP,"pause: /book NAME off","resume: /book NAME on"],("OK","books"))
def signals_text(cdb,n=8):
    import datetime as _dt
    rows=[]
    for ts,b,src,m,dec,why in cdb.q("select ts,book,source,mint,decision,reason from b_signals order by id desc limit ?",(n,)):
        rows+=[f"{_dt.datetime.fromtimestamp(ts).strftime('%H:%M')} {b:<9} {dec.upper()}",f"  {(m or '')[:4]}…{(m or '')[-4:]} from {str(src)[:14]}",kv("  why",why,6)]
    return term("signals --last",rows or ["no proposals yet"],("OK",f"last {n} gate decisions"))
def book_cmd(cdb,text):
    p=text.split()
    if len(p)!=3 or p[1] not in PAPER_BOOKS or p[2] not in("on","off"):return term("book",["usage: /book NAME on|off","books: "+", ".join(PAPER_BOOKS)],("WARN","bad syntax"))
    cdb.kv_set(p[1]+":paused",p[2]=="off")
    return term("book "+p[1],[kv("book",p[1]),kv("new buys","PAUSED" if p[2]=="off" else "allowed"),SEP,"exits and sells keep running"],("OK","book "+("paused" if p[2]=="off" else "resumed")))
def status():
    sel=db.kv_get("selected") or [];print("=== PAPER copy bot status ===")
    print("running PID:",open("bot.pid").read().strip() if os.path.exists("bot.pid") else "?","| STOP file:",stop_requested(),"| shutdown:",bool(db.kv_get("shutdown")))
    print("last selection:",dt.datetime.fromtimestamp(db.kv_get("selected_at",0)).strftime("%Y-%m-%d %H:%M"))
    for s in sel: print(f"  • {s['handle']} {s['wallet']}\n    {s['reason']}")
    if not sel: print("  (no trader qualified -> sitting in cash)")
    for b in PAPER_BOOKS:
        print(f"[{b}] cash ${db.kv_get(b+':cash',0) or 0:.2f}  equity ${db.kv_get(b+':last_equity',db.kv_get(b+':cash',0)) or 0:.2f}  peak ${db.kv_get(b+':peak',0) or 0:.2f}")
    for r in db.q("select book,trader,mint,tokens,cost,datetime(opened,'unixepoch','localtime') from b_positions where status='open'"): print("  open:",r)
    for r in db.q("select datetime(ts,'unixepoch','localtime'),book,trader,side,substr(mint,1,8),round(usd,2),round(pnl,2),note from b_trades order by ts desc limit 10"): print("  ",r)
    print("last poll:",dt.datetime.fromtimestamp(db.kv_get("last_poll",0)).strftime("%H:%M:%S"),"| helius calls this run:",db.kv_get("helius_calls"))
def run():
    open(os.environ.get("COPYBOT_PID","bot.pid"),"w").write(str(os.getpid()))
    hel,fomo=Helius(),Fomo();n=Notifier(db,cfg["telegram"]["enabled"] and not NO_TG);pp=Paper(db,hel,cfg,n,"copy")
    cons=Paper(db,hel,cfg,n,"consensus");tb=Paper(db,hel,cfg,n,"trend");books=[pp,cons,tb]
    from trend import TrendRunner,run_backtest_async,backtest_report
    tr_run=TrendRunner(db,tb,n,cfg) if book_enabled("trend") else None;lasttrend=0
    ccfg=(cfg.get("books") or {}).get("consensus") or {};recent={}   # mint -> {trader: buy time} for the consensus signal
    lastmark=0;lastexit=0
    ws=WalletStream(cfg['monitor'].get('commitment','confirmed'));ws.start()
    def reply(cmd,cdb,text=""):
        url_f=os.path.join(HERE,"dashboard","url.txt");tok_f=os.path.join(HERE,"dashboard","token.txt")
        url=(open(url_f).read().strip()+"/?t="+open(tok_f).read().strip()) if os.path.exists(url_f) and os.path.exists(tok_f) else None
        sel=cdb.kv_get("selected") or [];cash=cdb.kv_get("copy:cash",0) or 0;eq=cdb.kv_get("copy:last_equity",cash) or cash
        npos=cdb.q("select count(*) from b_positions where book='copy' and status='open'")[0][0]
        r=cdb.q("select count(*),coalesce(sum(pnl),0) from b_trades where book='copy' and side='sell'")[0]
        x=exec_stats(cdb,"copy");ms=lambda v:"–" if v is None else f"{v/1000:.1f}s"
        wsx=cdb.kv_get("ws") or {};shut=cdb.kv_get("copy:shutdown")
        rows=[kv("mode",PAPER),SEP,kv("equity",f"${eq:,.2f}"),kv("start",f"${cfg['paper']['start_equity_usd']:,.2f}"),kv("cash",f"${cash:,.2f}"),
            kv("open",npos),kv("sells",r[0]),kv("realized",usd(r[1],True)),SEP,
            kv("detect",ms(x['det_med_ms'])+" med"),kv("fill",ms(x['total_med_ms'])+" med"),
            kv("slip","–" if x['slip_avg_bps'] is None else format(x['slip_avg_bps'],'+.0f')+" bps"),
            kv("fails","–" if x['fail_rate'] is None else format(x['fail_rate'],'.0%')),
            kv("ws","🟢 UP" if wsx.get('connected') else "🔴 DOWN (polling)"),kv("exits",exits_text()),SEP,"watching:"]+(["  > "+x_["handle"] for x_ in sel] or ["  none qualified (cash)"])+[SEP,"all books: /books"]
        pend=cdb.kv_get("copy:pending_sells") or {}
        if cdb.kv_get("panic"): rows+=[SEP,"[ERR] PANIC STOP active"]
        if pend: rows+=[SEP,f"[WARN] {len(pend)} sell(s) retrying"]
        if shut: rows+=[SEP,"[WARN] drawdown shutdown active"]
        st_tag=("WARN","drawdown shutdown") if shut else ("OK","🟢 system nominal")
        st=term("status",rows,st_tag)
        link=f'<a href="{esc(url)}">open dashboard</a>' if url else None
        if cmd=="/version":return term("version",[kv("commit",version()),kv("mode",PAPER)],("OK","build info"))
        if cmd=="/update":
            last=cdb.kv_get("last_update_req",0)
            if time.time()-last<120:return term("update",[kv("asked",f"{int(time.time()-last)}s ago"),kv("retry in",f"{int(120-(time.time()-last))}s")],("WARN","update already requested"))
            cdb.kv_set("last_update_req",time.time());cdb.kv_set("pending_update",{"t":time.time(),"from":version()})
            import subprocess
            try:
                r=subprocess.run(["sudo","-n","/usr/local/sbin/copybot-selfupdate"],capture_output=True,text=True,timeout=60)
                if r.returncode!=0:
                    cdb.kv_set("pending_update",None);log.warning("selfupdate failed: %s %s",r.returncode,(r.stderr or r.stdout)[-300:])
                    return term("update",[((r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}"),SEP,"run `copybot update` once on","the server to install the","/update permission"],("ERR","update could not start"))
            except Exception as e:
                cdb.kv_set("pending_update",None);return term("update",[str(e)],("ERR","update could not start"))
            return term("git pull && restart",[kv("current",version()[:7]),kv("eta","~30-60s"),SEP,"bot will confirm on boot"],("OK","⚡ update started"))
        if cmd=="/health":return health_text()
        if cmd=="/rollback":
            last=cdb.kv_get("last_rollback_req",0)
            if time.time()-last<120:return term("rollback",[kv("asked",f"{int(time.time()-last)}s ago")],("WARN","rollback pending"))
            sn=_jload(os.path.join(STATE,"last_snapshot.json"))
            if not sn:return term("rollback",["no snapshot yet","(one is made on every /update)"],("ERR","nothing to roll back to"))
            cdb.kv_set("last_rollback_req",time.time())
            import subprocess
            try:
                r=subprocess.run(["sudo","-n","/usr/local/sbin/copybot-rollback"],capture_output=True,text=True,timeout=60)
                if r.returncode!=0:return term("rollback",[((r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}")],("ERR","rollback could not start"))
            except Exception as e:return term("rollback",[str(e)],("ERR","rollback could not start"))
            return term("rollback",[kv("current",version()[:7]),kv("target",str(sn.get("version","?"))[:7]),kv("eta","~30-90s"),SEP,"result arrives as a message"],("OK","⚡ rollback started"))
        if cmd=="/status":return st
        if cmd=="/books":return books_text(cdb)
        if cmd=="/signals":return signals_text(cdb)
        if cmd=="/book":return book_cmd(cdb,text)
        if cmd=="/trend":return term("trend --status",tr_run.status_rows() if tr_run else ["Trend Rider is switched off","(books.trend.enabled)"],("OK","📈 Trend Rider"))
        if cmd=="/backtest":
            last=cdb.kv_get("backtest:last")
            if text.strip().endswith("last") and last:return backtest_report(last)
            if not run_backtest_async(os.path.join(HERE,os.environ.get("COPYBOT_DB","bot.db")),n,cfg):return term("backtest",["a backtest is already running"],("WARN","busy"))
            return term("backtest --trend-rider",["downloading ~2 years of SOL","candles, then testing the","rules. First run: a few","minutes. Report follows.",SEP,"/backtest last = last report"],("OK","⏳ backtest started"))
        if cmd=="/dashboard":return term("dashboard",[kv("mode",PAPER),kv("access","token link (private)")],("OK","dashboard ready"),link) if url else term("dashboard",["dashboard URL not configured"],("ERR","no dashboard"))
        helprows=sum(([f"/{c}","  "+d] for c,d in COMMANDS),[])
        if cmd=="/start":return term("ssh zetrax",[kv("session","connected"),kv("mode",PAPER)],("OK","⚡ ZETRAX link established"))+"\n"+st+(("\n"+link) if link else "")+"\n"+term("help",helprows)
        return term("man zetrax",["paper copy-trading bot","watch-only","mode: "+PAPER,SEP]+helprows,("OK","help"))
    L=None
    if not NO_TG:
        try: L=CommandListener(os.path.join(HERE,os.environ.get("COPYBOT_DB","bot.db")),reply);L.start()
        except Exception: log.exception("command listener failed to start");L=None
    def hb_loop():
        # healthy = command listener alive (or Telegram intentionally off); watched by update.sh + watchdog
        while True:
            if L is None and NO_TG or L is not None and (not L.token or L.is_alive()): heartbeat()
            time.sleep(30)
    import threading;threading.Thread(target=hb_loop,daemon=True).start()
    # startup notices run AFTER the listener is up and can never crash the bot
    def startup_msgs():
        pk=db.kv_get("pending_setkey")
        if pk:
            db.kv_set("pending_setkey",None);ok,info=test_key(pk["name"])
            n.send(term("keytest "+pk['name'],[kv("key",pk['name']),kv("value",pk['masked']),kv("result",info)],("OK","🔑 key works") if ok else ("ERR","🔴 key test FAILED")))
        pend=db.kv_get("pending_update")
        if pend:
            db.kv_set("pending_update",None);v=version()
            n.send(term("update --verify",[kv("from",str(pend.get("from") or "?")[:7]),kv("now",v[:7])],("OK","updated to "+v[:7])) if v!=pend.get("from") else
                   term("update --verify",[kv("version",v[:7]),SEP,"version unchanged; check:","copybot logs","/var/log/copybot-update.log"],("WARN","restarted, no new code")))
        n.send(term("./zetrax --boot",[kv("version",version()[:7]),kv("status","online")]+pp.summary_rows(),("OK","⚡ ZETRAX online")))
    try: startup_msgs()
    except Exception: log.exception("startup notification failed (continuing)")
    while True:
        if stop_requested(): n.send(term("shutdown",["STOP file found","bot exiting"],("WARN","🔴 ZETRAX halted")));break
        if time.time()-db.kv_get("selected_at",0)>cfg["selection"]["reevaluate_hours"]*3600:
            old={s["handle"] for s in db.kv_get("selected") or []}
            sel=select(db,hel,fomo,cfg);db.kv_set("selected",sel);db.kv_set("selected_at",time.time())
            if {s["handle"] for s in sel}!=old or not old:
                n.send(term("select --traders",[kv("mode",PAPER),SEP]+(sum(([f"> {s['handle']}","  "+str(s['reason'])] for s in sel),[]) or ["none qualified","holding cash"]),("OK",f"{len(sel)} trader(s) selected") if sel else ("WARN","no trader qualified")))
        sel=db.kv_get("selected") or []
        watch={s["handle"]:s["wallet"] for s in sel}
        for trs, in db.q("select distinct trader from b_positions where status='open' and book in('copy','consensus')"):
            for tr in trs.split("+"):
                if tr not in watch:
                    w=db.q("select wallet from decisions where trader=? order by ts desc limit 1",(tr,))
                    if w:watch[tr]=w[0][0]
        ws.set_wallets(watch.values());w2t={w:t for t,w in watch.items()}
        sell_only=set(watch)-{x["handle"] for x in sel}
        def handle(w,sig,detect_ts):
            if db.seen(sig):return
            tr=w2t.get(w)
            if not tr:return
            t=hel.tx(sig);e=parse_tx(t,w) if t else None;c=classify(e,sol_usd()) if e else None
            if not c:return
            side,mnt,a,usd,pre=c;tm={"block_ts":t.get("blockTime"),"detect_ts":detect_ts}
            lat=(detect_ts-tm["block_ts"]) if tm["block_ts"] else float("nan")
            log.info("TRADER %s %s %s %.4g tokens $%.2f detect %.1fs sig %s",tr,side,mnt,a,usd,lat,sig[:12])
            if side=="buy" and tr not in sell_only:
                pp.on_buy(tr,mnt,usd,a,sig,tm)
                # consensus: 2+ watched traders buying the same coin within the window -> its own paper book
                win=ccfg.get("window_minutes",15)*60;now=time.time()
                r=recent.setdefault(mnt,{});r[tr]=now
                for k in [k for k,v in r.items() if now-v>win]:r.pop(k)
                for m_ in [m_ for m_,v in recent.items() if not v]:recent.pop(m_)
                if book_enabled("consensus") and len(r)>=ccfg.get("min_traders",2):
                    cons.on_buy("+".join(sorted(r)),mnt,None,a,sig,tm,meta={"traders":sorted(r)})
            elif side in("sell","xfer") and a<0:
                for b in books:b.on_sell(tr,mnt,(-a/pre) if pre else 1.0,usd,-a,sig,tm)
        # 1) real-time: drain websocket notifications until the next fallback poll is due
        interval=cfg["monitor"]["poll_seconds"] if not ws.connected else cfg["monitor"].get("fallback_poll_seconds",120)
        until=time.time()+interval
        while time.time()<until and not stop_requested():
            if time.time()-lastexit>=cfg.get("exits",{}).get("check_seconds",30):
                lastexit=time.time()
                for b in books:
                    try: b.check_exits()
                    except Exception: log.exception("exit check failed for %s (continuing)",b.book)
                if tr_run:
                    try: tr_run.tick()
                    except Exception: log.exception("trend tick failed (continuing)")
            try: w,sig,dts,slot=ws.q.get(timeout=1)
            except queue.Empty: continue
            try: handle(w,sig,dts)
            except Exception as ex: log.warning("ws handle %s: %s",sig[:12],ex)
        # 2) fallback polling (catches anything the websocket missed; dedupe by signature)
        for tr,w in watch.items():
            try:
                last=db.kv_get("last_sig_"+w);sigs=hel.new_sigs(w,last)
                if last is None:
                    db.kv_set("last_sig_"+w,sigs[0]["signature"] if sigs else "")
                    for x in sigs: db.seen(x["signature"])
                    log.info("monitoring %s %s from now",tr,w);continue
                if sigs: db.kv_set("last_sig_"+w,sigs[0]["signature"])
                for x in reversed(sigs):
                    if not x.get("err"): handle(w,x["signature"],time.time())
            except Exception as ex: log.warning("poll %s: %s",tr,ex)
        db.kv_set("ws",{"connected":ws.connected,"notifications":ws.notifications,"reconnects":ws.reconnects,"last_msg":ws.last_msg})
        db.kv_set("last_poll",time.time());db.kv_set("helius_calls",hel.calls)
        if time.time()-lastmark>300:
            for b in books:
                try: b.mark()
                except Exception: log.exception("mark failed for %s",b.book)
            lastmark=time.time()
if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "status"
    if cmd=="run":run()
    elif cmd=="status":status()
    elif cmd=="summary":print(Paper(db,None,cfg,Notifier(db,False)).summary())
    elif cmd=="select":sel=select(db,Helius(),Fomo(),cfg);db.kv_set("selected",sel);db.kv_set("selected_at",time.time());print(json.dumps(sel,indent=1))
