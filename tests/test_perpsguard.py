"""Perps guard (perpsguard/perpsguard.py) and the real-money perps module (perps_live.py) against a fake Hyperliquid
(no network, no SDK, no key). Checks every refusal of the guard, that closing is never blocked, the separate
permission (/live trial can not switch perps on), the tap approval, protection after the fill, partial fills,
reconciliation (TP1 -> breakeven, exit from the exchange's fills, unknown positions), panic. Run:
python3 tests/test_perpsguard.py"""
import sys,os,time,json,tempfile,re,html
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);sys.path.insert(0,os.path.join(R,"perpsguard"));os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(PERPS_STATE=T,PERPS_CONF=os.path.join(T,"guard.json"),COPYBOT_MEMORY=os.path.join(T,"mem"),HELIUS_API_KEY="x")
import perpsguard as G
res={}
AGENT="0x"+"a1"*20;MASTER="0x"+"b2"*20
G.keyfile=lambda:{"key":"0x"+"11"*32,"address":AGENT}
class FakeHL:
    """A tiny Hyperliquid: one account, positions, resting orders, fills."""
    def __init__(s,key,net,master):s.master=master;s.net=net;s.av=200.0;s.pos={};s.orders_={};s.oid=100;s.sent=[];s.lev={};s.fills_=[];s.fill_ratio=1.0;s.ok_approved=True
    def address(s):return AGENT
    def meta(s):return {c:dict(max_lev=m,sz_dec=d) for c,m,d in(("XRP",20,0),("DOGE",10,0),("SOL",20,2),("ATOM",5,2),("LINK",10,1),("UNI",10,1),("BTC",40,5))}
    def mids(s):return {"XRP":1.30,"DOGE":0.08,"SOL":186.0,"ATOM":1.7,"LINK":12.0,"UNI":7.2,"BTC":60000.0}
    def user(s):return {"marginSummary":{"accountValue":str(s.av)},"assetPositions":[{"position":{"coin":c,"szi":str(p["szi"]),"entryPx":str(p["entry"]),"leverage":{"value":s.lev.get(c,1)},"liquidationPx":None,"unrealizedPnl":"0"}} for c,p in s.pos.items() if p["szi"]]}
    def open_orders(s):return [dict(coin=o["coin"],oid=i,side="A" if not o["is_buy"] else "B",sz=str(o["sz"]),limitPx=str(o["limit_px"]),triggerPx=str(o["order_type"]["trigger"]["triggerPx"]) if "trigger" in o["order_type"] else None,
        reduceOnly=o["reduce_only"],orderType=("Stop Market" if o["order_type"].get("trigger",{}).get("tpsl")=="sl" else "Take Profit Limit") if "trigger" in o["order_type"] else "Limit") for i,o in s.orders_.items()]
    def fills(s,since):return s.fills_
    def leverage(s,coin,lev):s.lev[coin]=lev;return {"status":"ok"}
    def approved(s):return s.ok_approved
    def orders(s,reqs,grouping="na"):
        s.sent.append(reqs);st=[]
        for o in reqs:
            if "limit" in o["order_type"] and o["order_type"]["limit"]["tif"]=="Ioc":
                sz=o["sz"]*s.fill_ratio;d=1 if o["is_buy"] else -1;p=s.pos.setdefault(o["coin"],dict(szi=0.0,entry=o["limit_px"]))
                p["szi"]=round(p["szi"]+d*sz,8);p["entry"]=s.mids()[o["coin"]];st.append({"filled":{"totalSz":str(sz),"avgPx":str(s.mids()[o["coin"]]),"oid":s.oid}});s.oid+=1
            else:s.oid+=1;s.orders_[s.oid]=o;st.append({"resting":{"oid":s.oid}})
        return {"status":"ok","response":{"type":"order","data":{"statuses":st}}}
    def cancel(s,coin,oid):s.orders_.pop(int(oid),None);return {"status":"ok","response":{"data":{"statuses":["success"]}}}
FK=[]
def make(k,n,m):
    f=FakeHL(k,n,m);FK.append(f);return f
def H(req):
    try:out=dict(G.handle(req,make=make));out.setdefault("ok",True);return out      # like the guard's socket server
    except G.Refused as e:return {"ok":False,"error":str(e)}
def err(r,w):return r.get("ok") is False and w in r.get("error","")
G._hl.clear()
res["default network is testnet; a request can not change it"]=G.conf()["network"]=="test" and H(dict(op="address",network="main"))["network"]=="test"
res["no orders before the master address is set"]=err(H(dict(op="order",orders=[dict(coin="XRP",side="buy",sz=5)])),"master")
res["master must be an EVM address"]=err(H(dict(op="set_master",address="hello")),"EVM")
res["the master can not be the agent itself"]=err(H(dict(op="set_master",address=AGENT)),"YOUR wallet")
res["master set once"]=H(dict(op="set_master",address=MASTER)).get("master")==MASTER and err(H(dict(op="set_master",address="0x"+"c3"*20)),"already set")
f=lambda:FK[-1]
H(dict(op="status"));fk=f()
res["only allowed coins"]=err(H(dict(op="order",orders=[dict(coin="BTC",side="buy",sz=0.001)])),"not an allowed coin")
res["per-order notional cap ($25 default)"]=err(H(dict(op="order",orders=[dict(coin="XRP",side="buy",sz=30)])),"per-order limit")
r=H(dict(op="order",orders=[dict(coin="XRP",side="buy",sz=15)],lev=2))
res["an opening order inside the caps is sent, leverage set (isolated) first"]=r.get("ok") and fk.lev.get("XRP")==2 and fk.pos["XRP"]["szi"]==15
res["leverage over the guard cap (3x) refused"]=err(H(dict(op="order",orders=[dict(coin="DOGE",side="buy",sz=100)],lev=5)),"over the guard's 3x") and err(H(dict(op="leverage",coin="XRP",lev=4)),"3x limit")
res["limit price more than 5% from the market refused"]=err(H(dict(op="order",orders=[dict(coin="XRP",side="buy",sz=10,kind="ioc",px=1.5)])),"5% from")
res["take-profit / stop must be reduce-only"]=err(H(dict(op="order",orders=[dict(coin="XRP",side="sell",sz=15,kind="sl",trigger=1.25)])),"reduce-only")
r=H(dict(op="order",orders=[dict(coin="XRP",side="sell",sz=15,kind="sl",trigger=1.25,reduce_only=True),dict(coin="XRP",side="sell",sz=7,kind="tp",trigger=1.34,reduce_only=True)]))
sl=[o for o in fk.sent[-1] if o["order_type"]["trigger"]["tpsl"]=="sl"][0];tp=[o for o in fk.sent[-1] if o["order_type"]["trigger"]["tpsl"]=="tp"][0]
res["stop = market trigger, worst price 5% past it (a gap still closes); TP = limit AT the target"]=r.get("ok") and sl["order_type"]["trigger"]["isMarket"] and abs(sl["limit_px"]-1.25*0.95)<0.01 and not tp["order_type"]["trigger"]["isMarket"] and tp["limit_px"]==1.34
fk.av=8.0
res["total exposure over 3x the account refused"]=err(H(dict(op="order",orders=[dict(coin="DOGE",side="buy",sz=100)])),"over 3x the account")
fk.av=200.0
for i in range(4):H(dict(op="order",orders=[dict(coin="UNI",side="buy",sz=3.0)]))
res["daily opening limit ($100 default)"]=err(H(dict(op="order",orders=[dict(coin="UNI",side="buy",sz=3.0)])),"daily limit")
r=H(dict(op="order",orders=[dict(coin="XRP",side="sell",sz=15,kind="ioc",reduce_only=True)]))
res["closing (reduce-only) is never blocked, even after the daily limit"]=r.get("ok") and r["opening_usd"]==0 and fk.pos["XRP"]["szi"]==0
fk.orders_[999]=dict(coin="UNI",is_buy=False,sz=1,limit_px=8,order_type={"trigger":{"triggerPx":6,"isMarket":True,"tpsl":"sl"}},reduce_only=True)
r=H(dict(op="close_all"));kinds=[x[0] for x in r["result"]]
res["close_all: cancels every order and closes every position reduce-only"]=r.get("ok") and "cancel" in kinds and "close" in kinds and all(o["reduce_only"] for o in fk.sent[-1]) and not fk.orders_
res["sizes and prices are rounded to Hyperliquid's rules"]=G.rnd_sz(1.23456,2)==1.23 and G.rnd_px(1.234567,0)==1.2346 and G.rnd_px(186.123456,2)==186.12
json.dump(dict(network="main"),open(os.environ["PERPS_CONF"],"w"));G._hl.clear()
res["selftest refuses on mainnet"]=err(H(dict(op="selftest")),"testnet only")
json.dump(dict(network="test"),open(os.environ["PERPS_CONF"],"w"));G._hl.clear()
r=H(dict(op="selftest"));res["selftest on testnet: post-only order far below, then cancelled"]=r.get("ok") and any("cancelled" in x for x in r["steps"]) and not f().orders_
# ---- perps_live.py (bot side) with the guard as a plain function --------------------------------------------
import yaml,perps_live as PL
from db import DB
cfg=yaml.safe_load(open("config.yaml"));db=DB(os.path.join(T,"bot.db"));db.kv_set("boot_id","bootA");S=[]
class N:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):S.append((tier,html.unescape(re.sub(r"<[^>]+>","",t)),buttons))
def call(op,timeout=60,**k):
    r=H(dict(k,op=op))
    if not r.get("ok"):raise PL.GuardError(r.get("error"))
    return r
G.state(dict(day_usd=0,day_orders=0))            # a new UTC day for the guard's daily limits
L=PL.PerpsLive(db,cfg,N(),call=call);fk=f()
db.kv_set("live:armed",dict(ts=time.time(),boot="bootA",until=time.time()+86400,mode="trial"))
res["/live trial (Trend Rider) does NOT switch real-money perps on"]=not L.armed()
fk.ok_approved=False
t=L.arm(None,"trial");res["trial refused until the agent is approved and the selftest passed"]="not yet" in t and "approve the agent" in html.unescape(re.sub(r"<[^>]+>","",t))
fk.ok_approved=True;H(dict(op="selftest"));L=PL.PerpsLive(db,cfg,N(),call=call);fk=f()
t=L.arm(None,"arm");res["arm refused while the strategy is unproven (backtest, 28 days, 30 trades)"]="not proven" in t
t=L.arm(None,"trial");c=re.search(r"trial (\d{6})",html.unescape(re.sub(r"<[^>]+>","",t))).group(1)
res["trial needs the 6-digit code, then it is on"]="confirm" in t and "wrong" in L.arm("000000","trial") and "ON" in L.arm(c,"trial") and L.armed() and L.mode()=="trial"
db.kv_set("boot_id","bootB");res["a restart switches it off"]=not L.armed();db.kv_set("boot_id","bootA")
sg=dict(direction=1,entry_ref=1.30,stop=1.27,tp1=1.33,tp2=1.36,stop_dist=0.023,score=82,why="long: closed above the 20-candle high",t=int(time.time()))
import perps as PZ
r=L.consider("XRP",sg,fk.meta()["XRP"],PZ.BOOK_DEF["perps"])
ap=db.kv_get("perps-live:approval");btn=S[-1][2]
res["trial: every order waits for your tap (Approve / Reject buttons)"]=r=="pending" and ap and ap["status"]=="pending" and btn and btn[0][0][1].startswith("pappr:")
res["trial caps: margin <= $10, leverage <= 3x, notional <= guard's $25"]=ap["lev"]<=3 and ap["notional"]<=25 and ap["notional"]/ap["lev"]<=10.0001
# the tap, through the real Telegram callback handler
import notify
cl=notify.CommandListener(os.path.join(T,"bot.db"),None);cl.api=lambda *a,**k:{"ok":True};cl.say=lambda *a,**k:None;db.kv_set("tg_chat_id","42")
cl.on_callback(db,{"id":"q1","data":f"pappr:{ap['id']}:yes","message":{"chat":{"id":42}}})
res["the tap is the perps approval (not the Trend Rider one)"]=db.kv_get("perps-live:approval")["status"]=="yes" and db.kv_get("live:approval") is None
L.tick(fk.mids())
P=db.q("select mint,tokens,meta from b_positions where book='perps-live' and status='open'")
m=json.loads(P[0][2]) if P else {}
res["approved: IOC entry, then stop + TP1 50% + TP2 on the exchange, confirmed"]=bool(P) and m.get("sl_oid") and m.get("tp1_oid") and m.get("tp2_oid") and L.armed() and any("REAL perps entry confirmed" in x[1] for x in S)
# partial fill: protection sized to what filled
db.x("delete from b_positions where book='perps-live'");fk.pos={};fk.orders_={};fk.fill_ratio=0.5;db.kv_set("perps-live:approved_count",0);G.state(dict(day_usd=0,day_orders=0))
L.ks("armed",dict(L.k("armed") or {},mode="arm"));db.kv_set("perps-live:approved_count",99)
pid=L.execute(dict(coin="XRP",side=1,sz=10.0,lev=2,notional=13,ref=1.30,stop=1.27,tp1=1.33,tp2=1.36,score=80,why="x",note="",sz_dec=0))
sl=[o for o in fk.orders_.values() if o["order_type"]["trigger"]["tpsl"]=="sl"]
res["partial fill (5 of 10): stop and targets cover the 5 that filled"]=bool(pid) and sl and abs(sl[0]["sz"]-5)<1e-9 and abs(sum(o["sz"] for o in fk.orders_.values() if o["order_type"]["trigger"]["tpsl"]=="tp")-5)<1e-6
fk.fill_ratio=1.0
# TP1 fills on the exchange -> stop to breakeven
fk.pos["XRP"]["szi"]=3.0;[fk.orders_.pop(o) for o in [i for i,o in fk.orders_.items() if o["order_type"]["trigger"]["tpsl"]=="tp" and o["sz"]==2.0]];L.last_rec=0;L.tick(fk.mids())
m=json.loads(db.q("select meta from b_positions where id=?",(pid,))[0][0]);sl=[o for o in fk.orders_.values() if o["order_type"]["trigger"]["tpsl"]=="sl"]
res["TP1 filled -> the stop moves to breakeven on the exchange"]=m["tp1_done"] and len(sl)==1 and sl[0]["order_type"]["trigger"]["triggerPx"]>=1.30 and abs(sl[0]["sz"]-3.0)<1e-9
# position gone -> exit booked from the exchange's own fills
fk.pos["XRP"]["szi"]=0;fk.fills_=[dict(coin="XRP",dir="Close Long",px="1.33",sz="2",closedPnl="0.06",fee="0.002"),dict(coin="XRP",dir="Close Long",px="1.36",sz="3",closedPnl="0.165",fee="0.002")]
L.last_rec=0;L.tick(fk.mids())
row=db.q("select status,realized from b_positions where id=?",(pid,))[0]
res["exit booked from the exchange's fills (real P&L after fees) + ledger"]=row[0]=="closed" and abs(row[1]-(0.225-0.004))<1e-9 and any("REAL perps closed" in x[1] for x in S)
# protection missing -> closed at once, live off
class NoProt(FakeHL):
    def orders(s,reqs,grouping="na"):
        if any("trigger" in o["order_type"] for o in reqs):return {"status":"ok","response":{"data":{"statuses":[{"error":"rejected"}]*len(reqs)}}}
        return FakeHL.orders(s,reqs,grouping)
G._hl.clear();FK.clear()
mk2=lambda k,n,m:(FK.append(NoProt(k,n,m)) or FK[-1])
def call2(op,timeout=60,**k):
    try:r=dict(G.handle(dict(k,op=op),make=mk2));r.setdefault("ok",True)
    except G.Refused as e:raise PL.GuardError(str(e))
    return r
L2=PL.PerpsLive(db,cfg,N(),call=call2);S.clear()
pid2=L2.execute(dict(coin="DOGE",side=-1,sz=150,lev=2,notional=12,ref=0.08,stop=0.082,tp1=0.078,tp2=0.076,score=80,why="x",note=""))
res["protection not confirmed -> position closed at once + live OFF (critical alert)"]=FK[-1].pos["DOGE"]["szi"]==0 and not L2.armed() and any(t=="critical" for t,_,_ in S)
# unknown position on the exchange -> mismatch, off
G._hl.clear();FK.clear();L3=PL.PerpsLive(db,cfg,N(),call=call);L3.ks("armed",dict(ts=time.time(),boot="bootA",until=time.time()+86400,mode="arm",sizing="charter"));L3.ks("mismatch",None)
H(dict(op="status"));f().pos["LINK"]=dict(szi=3.0,entry=12.0);L3.last_rec=0;S.clear();L3.tick(f().mids())
res["a position the bot did not open -> mismatch, live OFF, critical alert"]=bool(L3.k("mismatch")) and not L3.armed() and any(t=="critical" for t,_,_ in S)
# panic
db.kv_set("perps-live:panic",True);L3.ks("mismatch",None);S.clear();L3.tick(f().mids())
res["/panic: real-money perps cancel everything and close every position"]=f().pos["LINK"]["szi"]==0 and any("perps PANIC" in x[1] for x in S)
src=open("main.py").read();res["/panic sets the perps close-all flag"]='cdb.kv_set("perps-live:panic",True)' in src
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
