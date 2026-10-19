"""Trader sources, the source scorecard, the agreement boost and the forward check of the picks, offline (no keys, no
network). Run: python3 tests/test_sources.py"""
import sys,os,time,json,tempfile,re,html
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_MEMORY=os.path.join(T,"memory"),TELEGRAM_BOT_TOKEN="",COPYBOT_DB=os.path.join(T,"s.db"),GMGN_API_KEY="")
import yaml,sources,scorecard,selection,ops,ledger
from db import DB
from tgfmt import plain,W
cfg=yaml.safe_load(open("config.yaml"));db=DB(os.environ["COPYBOT_DB"]);res={};DAY=86400;now=time.time()
def txt(t):return html.unescape(re.sub(r"<[^>]+>","",t))
def fits(t):return all(len(l)<=W for pre in re.findall(r"<pre>(.*?)</pre>",t,re.S) for l in html.unescape(pre).split("\n"))
W1="7VertkgF9KLhxxJXHX6uaWuoYZTP9LdGj2bWmVXVpump";W2="9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin";W3="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
# ---- families ------------------------------------------------------------------------------------------------------
res["source names map to families"]=[scorecard.family(x) for x in("fomo:7d","dexscreener-buyers(3 tok)","birdeye:1W","nansen:smart-money","gmgn:kol")]==["fomo","dexscreener_buyers","birdeye","nansen","gmgn"]
res["fomo:7d + fomo:30d count as ONE family; + gmgn = two"]=scorecard.families("fomo:7d,fomo:30d")==["fomo"] and scorecard.families("fomo:7d,gmgn:smartmoney")==["fomo","gmgn"]
# ---- GMGN adapter ---------------------------------------------------------------------------------------------------
res["GMGN: no key -> skipped, no network"]=sources.gmgn(db,None,None,cfg)==[]
CALLS=[]
def fake_get(u,timeout=20,headers=None):
    CALLS.append((u,headers or {}))
    if "/v1/user/smartmoney" in u:return {"code":0,"data":{"list":[{"maker":W1,"side":"buy","maker_info":{"tags":["smart_degen"]}},{"maker":W1,"side":"sell"},{"maker":"bad","side":"buy"},{"side":"buy"}]}}
    if "/v1/user/kol" in u:return {"code":0,"data":{"list":[{"maker":W2,"maker_info":{"tags":["kol"],"twitter_username":"x"}},{"maker":W1}]}}
    return {}
sources._get=fake_get;os.environ["GMGN_API_KEY"]="gmgn_test_key_123"
_sleep=time.sleep;sources.time.sleep=lambda s:None
got=sources.gmgn(db,None,None,cfg)
u0,h0=CALLS[0]
res["GMGN: smart money + KOL wallets, deduped, bad addresses dropped"]=[(g["wallet"],g["source"]) for g in got]==[(W1,"gmgn:smartmoney"),(W2,"gmgn:kol")]
res["GMGN: official OpenAPI route, Solana, timestamp + client_id, key in the X-APIKEY header (not the URL)"]=u0.startswith("https://openapi.gmgn.ai/v1/user/smartmoney?") and "chain=sol" in u0 and "timestamp=" in u0 and "client_id=" in u0 and h0.get("X-APIKEY")=="gmgn_test_key_123" and "gmgn_test_key_123" not in u0
sources._get=lambda u,timeout=20,headers=None:{"code":401,"error":"invalid api key"}
res["GMGN: an error answer is a skipped source, never a crash"]=sources.gmgn(db,None,None,cfg)==[]
os.environ["GMGN_API_KEY"]=""
# ---- collect(): dedupe, per-source cap, auto-off ----------------------------------------------------------------------
fakes={"fomo":lambda *a:[dict(handle="alice",wallet=W1,source="fomo:7d",sol_share=1.0)],
       "dexscreener_buyers":lambda *a:[dict(handle=f"dex:{i}",wallet=f"w{i:040d}",source="dexscreener-buyers(1 tok)",sol_share=1.0) for i in range(40)],
       "birdeye":lambda *a:[],"nansen":lambda *a:[],"gmgn":lambda *a:[dict(handle="gmgn:a",wallet=W1,source="gmgn:smartmoney",sol_share=1.0)]}
real_adapters=dict(sources.ADAPTERS);sources.ADAPTERS.update(fakes)
C2=json.loads(json.dumps(cfg));C2["sources"]["dexscreener_buyers"]["max_candidates"]=30
allc,stats=sources.collect(db,None,None,C2)
res["collect: the same wallet from two sources is kept once, both sources listed"]=[x["source"] for x in allc if x["wallet"]==W1]==["fomo:7d,gmgn:smartmoney"]
res["collect: each source's candidate cap applies"]=sum(1 for x in allc if x["source"].startswith("dex"))==30
db.kv_set("source_score",{"rows":{"dexscreener_buyers":{"status":"on","mult":0.5,"bad":1},"gmgn":{"status":"off","mult":0.5,"bad":2}}})
allc,stats=sources.collect(db,None,None,C2)
res["collect: a halved source gets half its cap (floor 10); a switched-off source is skipped"]=sum(1 for x in allc if x["source"].startswith("dex"))==15 and "auto-off" in stats["gmgn"] and all("gmgn" not in x["source"] for x in allc)
db.kv_set("source_score",None);sources.ADAPTERS.clear();sources.ADAPTERS.update(real_adapters)
# ---- agreement boost ---------------------------------------------------------------------------------------------------
mk=lambda h,src,pnl:(dict(handle=h,wallet=h,source=src),True,"PASS",dict(pnl=pnl))
ranked=selection.rank([mk("solo","fomo:7d",1050.0),mk("agree","fomo:30d,gmgn:smartmoney",1000.0),mk("far","birdeye:1W",1500.0),mk("bad","fomo:7d",-5.0)],cfg,3)
res["agreement: 2 sources beat 1 source of similar P&L (1000 vs 1050), not a much bigger one (1500)"]=[r[0]["handle"] for r in ranked]==["far","agree","solo"]
res["agreement: the boost is logged with the decision"]=ranked[1][3]["sources_n"]==2 and ranked[1][3]["rank_pnl"]==1100.0
# ---- scorecard -------------------------------------------------------------------------------------------------------
def decide(h,w,src,ts):db.x("insert into decisions values(?,?,?,?,?,?)",(ts,h,w,1,"PASS",json.dumps(dict(source=src,pnl=500))))
decide("alice",W1,"fomo:7d,gmgn:kol",now-20*DAY);decide("bob",W2,"dexscreener-buyers(2 tok)",now-20*DAY);decide("carol",W3,"birdeye:1W",now-20*DAY)
def sell(book,tr,pnl,ts):db.ins("b_trades",ts=ts,book=book,trader=tr,mint="M",side="sell",usd=5,tokens=1,price=1,pnl=pnl,sig="s",note="")
for i in range(6):sell("copy","bob",-1.0,now-10*DAY+i)
for i in range(6):sell("copy","alice",+0.5,now-9*DAY+i)
sell("consensus","alice+bob",2.0,now-5*DAY);sell("copy","carol",9.0,now-40*DAY)
sc=scorecard.compute(db,cfg,now)
res["scorecard: P&L after costs credited to the source(s) that suggested the trader"]=round(sc["dexscreener_buyers"]["pnl"],2)==-5.0 and round(sc["fomo"]["pnl"],2)==4.0 and round(sc["gmgn"]["pnl"],2)==4.0
res["scorecard: a consensus exit is split between its traders; exits older than 30 days don't count"]=sc["dexscreener_buyers"]["exits"]==7 and "birdeye" not in sc
S=[]
class N:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):S.append((tier,plain(t),digest))
    def flush_digest(s,every_s=3600,force=False):return 0
st1=scorecard.update(db,cfg,N(),now)
r=st1["rows"]["dexscreener_buyers"]
res["a losing source (5+ exits): cap halved, a digest note"]=r["mult"]==0.5 and r["status"]=="on" and any(t=="digest" and "halved" in x for t,x,_ in S)
scorecard.update(db,cfg,N(),now+DAY)
res["a second bad check sooner than 7 days changes nothing"]=scorecard.state(db)["rows"]["dexscreener_buyers"]["status"]=="on" and scorecard.state(db)["rows"]["dexscreener_buyers"]["bad"]==1
S.clear();scorecard.update(db,cfg,N(),now+8*DAY)
r=scorecard.state(db)["rows"]["dexscreener_buyers"]
res["two bad checks a week apart: the source is switched OFF (digest note says how to undo)"]=r["status"]=="off" and any("/sources reset dexscreener_buyers" in (d or "") for _,_,d in S)
res["profitable sources keep their full cap"]=scorecard.state(db)["rows"]["fomo"]["mult"]==1.0 and scorecard.cap(db,cfg,"fomo")==300
t=scorecard.text(db,cfg);res["/sources: the table fits the phone"]="dexscreener" in txt(t) and "OFF" in txt(t) and fits(t)
res["/sources reset NAME switches it back on"]=scorecard.reset(db,"dexscreener_buyers") and not scorecard.is_off(db,"dexscreener_buyers") and scorecard.mult(db,"dexscreener_buyers")==1.0
# only one source left: never switched off
db.kv_set("source_score",None);C3=json.loads(json.dumps(cfg))
for n in("dexscreener_buyers","birdeye","nansen","gmgn"):C3["sources"][n]["enabled"]=False
db.x("delete from b_trades");db.x("delete from decisions");decide("alice",W1,"fomo:7d",now-20*DAY)
for i in range(6):sell("copy","alice",-1.0,now-3*DAY+i)
scorecard.update(db,C3,N(),now);scorecard.update(db,C3,N(),now+8*DAY)
res["the last source still on is never switched off (only its cap shrinks)"]=scorecard.state(db)["rows"]["fomo"]["status"]=="on" and scorecard.state(db)["rows"]["fomo"]["mult"]<1
# ---- forward check of the picks ---------------------------------------------------------------------------------------
db.x("delete from decisions");db.kv_set("fwd:done",None);db.kv_set("fwd:history",None)
p0=now-35*DAY
decide("alice",W1,"fomo:7d",p0);decide("alice",W1,"fomo:7d",p0+3600);decide("bob",W2,"gmgn:kol",p0)
def ev(w,t,mint,side,usd_,amt):
    e=dict(sig=f"{w[:4]}{t}{side}",t=t,mint=mint,side=side,usd=usd_,amt=amt,sol=None)
    db.c.execute("insert or ignore into events values(?,?,?,?)",(e["sig"],w,t,json.dumps(e)));db.c.commit()
import chain
ROUND=[]
def fake_rt(evs,solp,min_cost):
    out=[]
    for e in sorted(evs,key=lambda e:e["t"]):
        if e["side"]=="buy":ROUND.append(e)
        elif ROUND:b=ROUND.pop(0);out.append(dict(mint=e["mint"],open=b["t"],close=e["t"],cost=b["usd"],pnl=e["usd"]-b["usd"]))
    return out
chain.roundtrips=fake_rt;chain.sol_usd=lambda:150.0
ev(W1,p0+DAY,"A","buy",10,1);ev(W1,p0+2*DAY,"A","sell",13,1)        # alice: +30% after the pick
ev(W2,p0+DAY,"B","buy",10,1);ev(W2,p0+2*DAY,"B","sell",9,1)         # bob: -10% after the pick
ev(W1,p0-5*DAY,"C","buy",10,1);ev(W1,p0-4*DAY,"C","sell",30,1)      # before the pick: not counted
res["picks: one per wallet per day"]=len(scorecard.picks(db,p0-DAY))==2
cfgF=json.loads(json.dumps(cfg));cfgF["paper"]["cost_model"]="fixed"          # the old 2%-per-side model: 4% a round trip
v=scorecard.forward(db,cfgF,None,now)
cost=scorecard.copy_cost(db,cfgF)
res["forward check: each pick judged on trades AFTER it, minus our copy costs (4% fees)"]=abs(cost-0.04)<1e-9 and v["d7"]["judged"]==2 and v["d7"]["kept_winning"]==1 and abs(v["d7"]["mean"]-((0.30-0.04)+(-0.10-0.04))/2)<1e-9
res["forward check: 30-day verdict too, by source"]=v["d30"]["judged"]==2 and set(v["d30"]["by_source"])=={"fomo","gmgn"}
res["forward check: a pick is never judged twice"]=scorecard.forward(db,cfgF,None,now+3600) is None
cm=scorecard.copy_cost(db,cfg,sol_px=150.0)
res["forward check with the measured model: margin + network fees + coin rent at our copy size"]=abs(cm-(2*0.0025+((2*(5000+100000)/1e9+0.00203928)*150.0)/5.0))<1e-9
L=open(os.path.join(os.environ["COPYBOT_MEMORY"],"Learnings.md")).read()
res["the verdict is written into Learnings.md (kept through the 25-trade rewrites)"]="forward check" in L and "kept winning" in L
ledger.write_learnings(db);res["a later Learnings.md rewrite keeps the section"]="forward check" in open(os.path.join(os.environ["COPYBOT_MEMORY"],"Learnings.md")).read()
# ---- daily and weekly jobs from ops.tick -------------------------------------------------------------------------------
db.kv_set("ops:scorecard",None);db.kv_set("ops:fwdcheck",None);db.kv_set("ops:morning","x");db.kv_set("ops:monthly",time.strftime("%Y-%m",time.gmtime()))
monday=now//DAY*DAY;monday-=((time.gmtime(monday).tm_wday)*DAY);t9=monday+9*3600;ran=[]
db.kv_set("ops:morning",time.strftime("%Y-%m-%d",time.gmtime(t9)))
ops.tick(db,cfg,N(),{},now=t9,weekly=lambda:ran.append(1));ops.tick(db,cfg,N(),{},now=t9+60,weekly=lambda:ran.append(1))
res["ops: scorecard once a day, forward check once a week (Monday)"]=db.kv_get("ops:scorecard")==time.strftime("%Y-%m-%d",time.gmtime(t9)) and ran==[1]
src=open("main.py").read()
res["main.py: the weekly check runs in its own thread (never delays exits)"]="forward_async(DBP,cfg,n)" in src and "threading.Thread" in open("scorecard.py").read()
# ---- keys, report, dashboard -------------------------------------------------------------------------------------------
import notify
res["GMGN_API_KEY allowed by /setkey and by the root helper, with a live key test"]="GMGN_API_KEY" in notify.ALLOWED_KEYS and "GMGN_API_KEY" in open("system/copybot-setkey").read() and 'name=="GMGN_API_KEY"' in src
res["config: gmgn source (skipped without a key) + scorecard settings"]=cfg["sources"]["gmgn"]["limit"]==50 and cfg["sources"]["scorecard"]["window_days"]==30
import report;rt=report.text(db,cfg);res["/report shows the per-source table"]="trader sources" in txt(rt) and fits(rt)
res["dashboard: sources scorecard panel"]="sources=dict(score=" in open("dashboard/server.py").read() and 'id="srt"' in open("dashboard/index.html").read() and "renderSources(BR.sources" in open("dashboard/index.html").read()
import tgfmt;res["Telegram menu has /sources"]=any(c=="sources" for c,_ in tgfmt.COMMANDS)
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
