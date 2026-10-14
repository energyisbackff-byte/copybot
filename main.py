#!/usr/bin/env python3
"""Paper copy-trading bot. Usage: main.py run | status | summary | select"""
import os,sys,time,json,queue,logging,yaml,datetime as dt
HERE=os.path.dirname(os.path.abspath(__file__));os.chdir(HERE);sys.path.insert(0,HERE)
from db import DB;from chain import Helius,Fomo,classify,parse_tx,sol_usd;from notify import Notifier,CommandListener;from paper import Paper;from selection import select;from wsmon import WalletStream
import execution  # disabled stub, never called in paper mode
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s",handlers=[logging.FileHandler("bot.log"),logging.StreamHandler()])
log=logging.getLogger("bot");cfg=yaml.safe_load(open("config.yaml"));db=DB(os.environ.get("COPYBOT_DB","bot.db"))
assert cfg.get("mode")=="paper","only paper mode supported"
NO_TG=os.environ.get("COPYBOT_NO_TELEGRAM")=="1"
def version():
    try:return open(os.path.join(HERE,"VERSION")).read().strip() or "unknown"
    except Exception:return "unknown"
def stop_requested(): return os.path.exists(cfg["safeguards"]["stop_file"])
def status():
    sel=db.kv_get("selected") or [];print("=== PAPER copy bot status ===")
    print("running PID:",open("bot.pid").read().strip() if os.path.exists("bot.pid") else "?","| STOP file:",stop_requested(),"| shutdown:",bool(db.kv_get("shutdown")))
    print("last selection:",dt.datetime.fromtimestamp(db.kv_get("selected_at",0)).strftime("%Y-%m-%d %H:%M"))
    for s in sel: print(f"  • {s['handle']} {s['wallet']}\n    {s['reason']}")
    if not sel: print("  (no trader qualified -> sitting in cash)")
    print(f"cash ${db.kv_get('cash',0):.2f}  equity ${db.kv_get('last_equity',db.kv_get('cash',0)):.2f}  peak ${db.kv_get('peak',0):.2f}")
    for r in db.q("select trader,mint,tokens,cost,datetime(opened,'unixepoch','localtime') from positions where status='open'"): print("  open:",r)
    for r in db.q("select datetime(ts,'unixepoch','localtime'),trader,side,substr(mint,1,8),round(usd,2),round(pnl,2),note from trades order by ts desc limit 10"): print("  ",r)
    print("last poll:",dt.datetime.fromtimestamp(db.kv_get("last_poll",0)).strftime("%H:%M:%S"),"| helius calls this run:",db.kv_get("helius_calls"))
def run():
    open(os.environ.get("COPYBOT_PID","bot.pid"),"w").write(str(os.getpid()))
    hel,fomo=Helius(),Fomo();n=Notifier(db,cfg["telegram"]["enabled"] and not NO_TG);pp=Paper(db,hel,cfg,n)
    pend=db.kv_get("pending_update")
    if pend:
        db.kv_set("pending_update",None);v=version()
        n.send(("✅ Updated to commit "+v[:7] if v!=pend.get("from") else "⚠️ Restarted but version unchanged ("+v[:7]+") - check: copybot logs / /var/log/copybot-update.log"))
    n.send("🤖 Paper copy bot started (NO real trades) · version "+version()[:7]+". "+pp.summary())
    lastmark=0
    ws=WalletStream(cfg['monitor'].get('commitment','confirmed'));ws.start()
    def reply(cmd,cdb):
        url_f=os.path.join(HERE,"dashboard","url.txt");tok_f=os.path.join(HERE,"dashboard","token.txt")
        url=(open(url_f).read().strip()+"/?t="+open(tok_f).read().strip()) if os.path.exists(url_f) and os.path.exists(tok_f) else None
        sel=cdb.kv_get("selected") or [];cash=cdb.kv_get("cash",0);eq=cdb.kv_get("last_equity",cash)
        npos=cdb.q("select count(*) from positions where status='open'")[0][0]
        r=cdb.q("select count(*),coalesce(sum(pnl),0) from trades where side='sell'")[0]
        st=(f"📋 PAPER bot status (no real trades)\nequity ${eq:.2f} (start ${cfg['paper']['start_equity_usd']}) cash ${cash:.2f}\n"
            f"open positions {npos} · closed sells {r[0]} · realized ${r[1]:+.2f}\nwatching: "+(", ".join(x["handle"] for x in sel) or "none qualified (cash)")
            +("\n⚠️ drawdown shutdown active" if cdb.kv_get("shutdown") else ""))
        from paper import exec_stats
        x=exec_stats(cdb);ms=lambda v:"–" if v is None else f"{v/1000:.1f}s"
        st+=f"\n⚡ detect median {ms(x['det_med_ms'])} · fill median {ms(x['total_med_ms'])} · slip vs trader {'–' if x['slip_avg_bps'] is None else format(x['slip_avg_bps'],'+.0f')+' bps'} · failures {'–' if x['fail_rate'] is None else format(x['fail_rate'],'.0%')}"
        wsx=cdb.kv_get("ws") or {};st+=f"\n📡 websocket {'UP' if wsx.get('connected') else 'DOWN (polling)'}"
        if cmd=="/version":return "🔖 version "+version()
        if cmd=="/update":
            last=cdb.kv_get("last_update_req",0)
            if time.time()-last<120:return f"⏳ Update already requested {int(time.time()-last)}s ago - try again in {int(120-(time.time()-last))}s."
            cdb.kv_set("last_update_req",time.time());cdb.kv_set("pending_update",{"t":time.time(),"from":version()})
            import subprocess
            try:
                r=subprocess.run(["sudo","-n","/usr/local/sbin/copybot-selfupdate"],capture_output=True,text=True,timeout=60)
                if r.returncode!=0:
                    cdb.kv_set("pending_update",None);log.warning("selfupdate failed: %s %s",r.returncode,(r.stderr or r.stdout)[-300:])
                    return "❌ Update could not start: "+((r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}")+"\n(run `copybot update` once on the server to install the /update permission)"
            except Exception as e:
                cdb.kv_set("pending_update",None);return f"❌ Update could not start: {e}"
            return f"🔄 Updating… (current {version()[:7]}). The bot restarts in ~30-60s and will confirm."
        if cmd=="/status":return st
        if cmd=="/dashboard":return ("📊 Dashboard: "+url) if url else "Dashboard URL not configured."
        if cmd=="/start":return "🤖 Copy bot connected - PAPER MODE, no real trades.\n\n"+st+("\n\n📊 "+url if url else "")+"\n\nCommands: /status /dashboard /version /update /help"
        return "Paper copy-trading bot (watch-only). /status · /dashboard · /version · /update"
    if not NO_TG: CommandListener(os.path.join(HERE,os.environ.get("COPYBOT_DB","bot.db")),reply).start()
    while True:
        if stop_requested(): n.send("🛑 STOP file found - bot exiting.");break
        if time.time()-db.kv_get("selected_at",0)>cfg["selection"]["reevaluate_hours"]*3600:
            old={s["handle"] for s in db.kv_get("selected") or []}
            sel=select(db,hel,fomo,cfg);db.kv_set("selected",sel);db.kv_set("selected_at",time.time())
            if {s["handle"] for s in sel}!=old or not old:
                n.send("🔁 Trader selection:\n"+("\n".join(f"• {s['handle']}: {s['reason']}" for s in sel) or "none qualified - holding cash"))
        sel=db.kv_get("selected") or []
        watch={s["handle"]:s["wallet"] for s in sel}
        for tr, in db.q("select distinct trader from positions where status='open'"):
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
            if side=="buy" and tr not in sell_only:pp.on_buy(tr,mnt,usd,a,sig,tm)
            elif side in("sell","xfer") and a<0:pp.on_sell(tr,mnt,(-a/pre) if pre else 1.0,usd,-a,sig,tm)
        # 1) real-time: drain websocket notifications until the next fallback poll is due
        interval=cfg["monitor"]["poll_seconds"] if not ws.connected else cfg["monitor"].get("fallback_poll_seconds",120)
        until=time.time()+interval
        while time.time()<until and not stop_requested():
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
        if time.time()-lastmark>300: pp.mark();lastmark=time.time()
if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "status"
    if cmd=="run":run()
    elif cmd=="status":status()
    elif cmd=="summary":print(Paper(db,None,cfg,Notifier(db,False)).summary())
    elif cmd=="select":sel=select(db,Helius(),Fomo(),cfg);db.kv_set("selected",sel);db.kv_set("selected_at",time.time());print(json.dumps(sel,indent=1))
