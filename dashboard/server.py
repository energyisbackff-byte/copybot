#!/usr/bin/env python3
"""Read-only dashboard for the paper copy bot. Never imports execution. Opens bot.db read-only."""
import os,json,time,sqlite3,urllib.request,hmac
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
HERE=os.path.dirname(os.path.abspath(__file__));DB=os.path.join(HERE,"..","bot.db")
TOKEN=open(os.path.join(HERE,"token.txt")).read().strip();START=52.0;PORT=int(os.environ.get("DASH_PORT","8787"))
HTML=open(os.path.join(HERE,"index.html")).read()
_pc={}
def prices(mints):
    need=[m for m in mints if time.time()-_pc.get(m,(0,0))[0]>15]
    for i in range(0,len(need),50):
        try:
            r=json.load(urllib.request.urlopen(urllib.request.Request("https://lite-api.jup.ag/price/v3?ids="+",".join(need[i:i+50]),headers={"user-agent":"dash"}),timeout=8))
            for m in need[i:i+50]: _pc[m]=(time.time(),(r.get(m) or {}).get("usdPrice"))
        except Exception: pass
    return {m:_pc.get(m,(0,None))[1] for m in mints}
def state():
    c=sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=5);q=lambda s,a=():c.execute(s,a).fetchall()
    kv={k:json.loads(v) for k,v in q("select k,v from kv where k in ('cash','peak','last_equity','selected','selected_at','shutdown','last_poll')")}
    eq=[(t,e) for t,e in q("select ts,equity from equity order by ts")]
    pk=START;dd=0;mdd=0
    for _,e in eq:pk=max(pk,e);mdd=max(mdd,(pk-e)/pk)
    sells=q("select pnl from trades where side='sell'");w=sum(p>0 for p, in sells);l=sum(p<=0 for p, in sells)
    pos=q("select trader,mint,tokens,cost,opened from positions where status='open'");px=prices([p[1] for p in pos])
    P=[];unrl=0
    for tr,m,tok,cost,op in pos:
        cur=px.get(m);val=tok*cur if cur else None;u=(val-cost) if val is not None else None;unrl+=u or 0
        ent=q("select price from trades where side='buy' and trader=? and mint=? order by ts desc limit 1",(tr,m))
        P.append(dict(trader=tr,mint=m,tokens=tok,cost=cost,entry=ent[0][0] if ent else None,price=cur,value=val,upnl=u,opened=op))
    cash=kv.get("cash",START);equity=cash+sum((p["value"] if p["value"] is not None else p["cost"]) for p in P)
    ts=q("select max(ts) from decisions")[0][0];dec={t:(json.loads(m),r,s) for t,m,r,s in q("select trader,metrics,reason,selected from decisions where ts=?",(ts,))} if ts else {}
    sel=kv.get("selected") or [];T=[]
    for s in sel:
        m=dec.get(s["handle"],({},"",1))[0];our=q("select coalesce(sum(pnl),0),count(*) from trades where side='sell' and trader=?",(s["handle"],))[0]
        T.append(dict(handle=s["handle"],wallet=s["wallet"],pnl30=m.get("pnl"),win=m.get("win"),pf=m.get("pf"),trades=m.get("trades"),our_pnl=our[0],our_trades=our[1],status="watching"))
    log=[]
    for t,tr,r,s in q("select ts,trader,reason,selected from decisions where selected=1 order by ts desc limit 15"): log.append(dict(ts=t,kind="select",text=f"SELECTED {tr}: {r}"))
    for t,tr,m,side,usd,pnl,note in q("select ts,trader,mint,side,usd,pnl,note from trades order by ts desc limit 60"):
        txt={"buy":f"BUY {m[:6]}… ${usd:.2f} copying {tr}","sell":f"SELL {m[:6]}… ${usd:.2f} PnL {pnl:+.2f} ({tr})","skip":f"SKIP {m[:6]}… from {tr}: {note}"}[side]
        log.append(dict(ts=t,kind=side,text=txt))
    if kv.get("shutdown"):log.append(dict(ts=time.time(),kind="guard",text="SAFEGUARD: drawdown auto-shutdown active"))
    log.sort(key=lambda x:-x["ts"])
    return dict(now=time.time(),start=START,cash=cash,equity=equity,peak=kv.get("peak",START),pnl=equity-START,unrealized=unrl,maxdd=mdd,
        wins=w,losses=l,equity_curve=eq[-2000:],positions=P,traders=T,log=log[:60],last_poll=kv.get("last_poll"),shutdown=bool(kv.get("shutdown")),
        live=bool(kv.get("last_poll")) and time.time()-kv.get("last_poll",0)<120)
class H(BaseHTTPRequestHandler):
    def log_message(s,*a):pass
    def do_GET(s):
        u=urlparse(s.path);t=(parse_qs(u.query).get("t") or [""])[0]
        if not hmac.compare_digest(t,TOKEN): return s._send(403,"text/plain",b"forbidden")
        if u.path=="/":return s._send(200,"text/html; charset=utf-8",HTML.encode())
        if u.path=="/api/state":
            try:return s._send(200,"application/json",json.dumps(state()).encode())
            except Exception as e:return s._send(500,"application/json",json.dumps({"error":str(e)}).encode())
        s._send(404,"text/plain",b"not found")
    def _send(s,code,ct,b):
        s.send_response(code);s.send_header("content-type",ct);s.send_header("cache-control","no-store");s.send_header("referrer-policy","no-referrer");s.end_headers();s.wfile.write(b)
ThreadingHTTPServer(("127.0.0.1",PORT),H).serve_forever()
