#!/usr/bin/env python3
"""Paper copy-trading bot. Usage: main.py run | status | summary | select"""
import os,sys,time,json,logging,yaml,datetime as dt
HERE=os.path.dirname(os.path.abspath(__file__));os.chdir(HERE);sys.path.insert(0,HERE)
from db import DB;from chain import Helius,Fomo,classify,parse_tx,sol_usd;from notify import Notifier;from paper import Paper;from selection import select
import execution  # disabled stub, never called in paper mode
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s",handlers=[logging.FileHandler("bot.log"),logging.StreamHandler()])
log=logging.getLogger("bot");cfg=yaml.safe_load(open("config.yaml"));db=DB("bot.db")
assert cfg.get("mode")=="paper","only paper mode supported"
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
    open("bot.pid","w").write(str(os.getpid()))
    hel,fomo=Helius(),Fomo();n=Notifier(db,cfg["telegram"]["enabled"]);pp=Paper(db,hel,cfg,n)
    n.send("🤖 Paper copy bot started (NO real trades). "+pp.summary())
    lastmark=0
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
        solp=sol_usd()
        for tr,w in watch.items():
            try:
                last=db.kv_get("last_sig_"+w)
                sigs=hel.new_sigs(w,last)
                if last is None:
                    db.kv_set("last_sig_"+w,sigs[0]["signature"] if sigs else "");log.info("monitoring %s %s from now",tr,w);continue
                if sigs: db.kv_set("last_sig_"+w,sigs[0]["signature"])
                for s in reversed(sigs):
                    if s.get("err") or db.seen(s["signature"]):continue
                    t=hel.tx(s["signature"]);e=parse_tx(t,w) if t else None;c=classify(e,solp) if e else None
                    if not c:continue
                    side,m,a,usd,pre=c;log.info("TRADER %s %s %s %.4g tokens $%.2f sig %s",tr,side,m,a,usd,s["signature"][:12])
                    if side=="buy" and tr in {x["handle"] for x in sel}:pp.on_buy(tr,m,usd,s["signature"])
                    elif side in("sell","xfer") and a<0:pp.on_sell(tr,m,(-a/pre) if pre else 1.0,s["signature"])
            except Exception as ex: log.warning("poll %s: %s",tr,ex)
        def reply(cmd):
            url=open("dashboard/url.txt").read().strip()+"/?t="+open("dashboard/token.txt").read().strip() if os.path.exists("dashboard/url.txt") else "dashboard not running"
            if cmd=="/dashboard":return "📊 Dashboard (paper mode): "+url+"\nOr tap the Dashboard menu button."
            if cmd=="/status":return pp.summary("📋 Status")+"\nwatching: "+", ".join(x["handle"] for x in sel)
            return "Paper copy bot (no real trades). Commands: /status /dashboard"
        n.commands(reply)
        db.kv_set("last_poll",time.time());db.kv_set("helius_calls",hel.calls)
        if time.time()-lastmark>300: pp.mark();lastmark=time.time()
        time.sleep(cfg["monitor"]["poll_seconds"])
if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "status"
    if cmd=="run":run()
    elif cmd=="status":status()
    elif cmd=="summary":print(Paper(db,None,cfg,Notifier(db,False)).summary())
    elif cmd=="select":sel=select(db,Helius(),Fomo(),cfg);db.kv_set("selected",sel);db.kv_set("selected_at",time.time());print(json.dumps(sel,indent=1))
