"""Narrative engine + Narrative Rider on made-up feeds (no network): parsers, weights, velocity vs baseline,
acceleration, sybil filter, clustering, life stages, coin mapping, 0-100 score, A+ gate, evidence, the rider's buy
and time-stop. Run: python3 tests/test_narrative.py"""
import sys,os,time,json,tempfile,math
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_MEMORY=os.path.join(T,"memory"),X_BEARER_TOKEN="")
import yaml,feeds as FD,narrative as NR,rider as RD,paper,veto,chain,risk
from db import DB
from tgfmt import plain
res={}
# ---- parsers --------------------------------------------------------------------------------------------
rss=b"""<?xml version="1.0"?><rss><channel><item><title>XRP surges after ETF approval</title><link>https://x.test/1</link>
<pubDate>Sun, 18 Oct 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Ripple wins&lt;/p&gt;</description></item><item><title></title></item></channel></rss>"""
it=FD.parse_rss(rss,"coindesk")
res["RSS: title, link, time, clean summary; empty items dropped"]=len(it)==1 and it[0]["title"].startswith("XRP") and abs(it[0]["ts"]-1792317600)<1 and it[0]["summary"]=="Ripple wins"
xj={"data":[{"id":"9","text":"$BONK and $WIF to the moon","author_id":"u1","created_at":"2026-10-18T10:00:00.000Z","public_metrics":{"like_count":10,"retweet_count":2,"reply_count":1,"quote_count":0},
     "entities":{"cashtags":[{"tag":"bonk"},{"tag":"WIF"}],"hashtags":[{"tag":"Solana"}]}}],
    "includes":{"users":[{"id":"u1","username":"alice","created_at":"2020-01-01T00:00:00Z","public_metrics":{"followers_count":5000},"verified":False}]},"meta":{"newest_id":"9"}}
p=FD.parse_x(xj)[0]
res["X: cashtags, author, followers, account age, engagement"]=p["cashtags"]==["BONK","WIF"] and p["author"]=="alice" and p["followers"]==5000 and p["account_age_days"]>2000 and p["engagement"]==10+4+1
rows=[(i*900,1,1.1,0.9,1.0+(0.01 if i%2 else 0),1) for i in range(30)]
a=FD.atr_from_rows(rows,14);res["ATR from candles (Wilder) as a share of price"]=a is not None and 0.15<a<0.25 and FD.atr_from_rows(rows[:10]) is None
gj={"data":[{"id":"solana_P1","attributes":{"address":"P1","name":"BONK / SOL","base_token_price_usd":"0.00002","reserve_in_usd":"900000","pool_created_at":"2026-10-01",
     "volume_usd":{"m5":"10","h1":"50000","h6":"60000","h24":"200000"},"price_change_percentage":{"m5":"1.0","h1":"12.5","h6":"20","h24":"30"},"transactions":{"h1":{"buys":50,"sells":20,"buyers":40}}},
     "relationships":{"base_token":{"data":{"id":"solana_MINTB","type":"token"}}}}],"included":[{"id":"solana_MINTB","attributes":{"symbol":"bonk","name":"Bonk"}}]}
g=FD.Feeds.gt_pools(gj)[0]
res["GeckoTerminal pools: mint from relationships, % to fractions"]=g["mint"]=="MINTB" and g["symbol"]=="BONK" and abs(g["ch"]["h1"]-0.125)<1e-9 and g["vol"]["h1"]==50000 and g["liq"]==900000
res["extract: cashtags, coin names, known capitals (not lowercase words)"]=NR.extract("Ripple and $wif pump; BONK rips, bonk",{"BONK"})=={"XRP","WIF","BONK"}
res["sentiment: ETF approval is bullish, hack is bearish"]=NR.sentiment(["XRP surges after ETF approval"])>0>NR.sentiment(["exchange hacked, XRP plunges"])
res["quality weight: new tiny account << old big account with a good record"]=NR.quality_weight(20,10)<0.1<1.0<NR.quality_weight(100000,2000,10,8)
# ---- fake outside world ------------------------------------------------------------------------------------
NOW={"t":1792310400.0}   # a Sunday 04:00 UTC
class F:
    def __init__(s,db):s.db=db;s.posts=[];s.items=[];s.metas=[];s.pairs={};s.tok={};s.trend=[];s.xon=True
    def x_enabled(s):return s.xon
    def x_budget_left(s):return 300
    def x_search(s,name,q,want=100):
        out=[p for p in s.posts if p.get("q",name)==name];s.posts=[p for p in s.posts if p not in out];return out
    def news(s):return s.items
    def ds_metas(s):return s.metas
    def ds_meta_pairs(s,slug):return s.pairs.get(slug,[])
    def gt_trending(s):return s.trend
    def gt_new_pools(s):return getattr(s,"newp",[])
    def ds_boosts(s):return {}
    def ds_search(s,sym):return None
    def ds_tokens(s,mints):return {m:s.tok[m] for m in mints if m in s.tok}
    def gaps(s,h=24):return []
    def prune(s,d=None):pass
def pair(mint,sym,h1=0.15,m5=0.01,v1=300000,v6=300000,liq=600000,age_h=48):
    return dict(mint=mint,symbol=sym,name=sym,pair="P"+mint,dex="raydium",price=1.0,vol=dict(m5=1,h1=v1,h6=v6,h24=v6*3),ch=dict(m5=m5,h1=h1,h6=0.2,h24=0.3),
                liq=liq,txns={},created=NOW["t"]-age_h*3600,mcap=1e7,socials=["twitter"],boosted=False)
def post(author,text,tags,followers=20000,age=900,eng=30,q="memes"):
    return dict(id=str(time.time_ns()),ts=NOW["t"]-60,text=text,author=author,followers=followers,account_age_days=age,verified=False,engagement=eng,cashtags=tags,hashtags=[],q=q)
db=DB(os.path.join(T,"n.db"));cfg=yaml.safe_load(open("config.yaml"));fk=F(db)
E=NR.Engine(db,cfg,None,feeds=fk)
# a quiet day: 1 mention an hour of $CATAI for 24h (the baseline)
for h in range(1,25):E.add_mention(NOW["t"]-h*3600-1800,"x:memes","x","CATAI","old%d"%h,1.0,5,f"cat ai thing {h}",NOW["t"])
E.flush()
fk.metas=[dict(slug="ai-cats",name="AI cats",desc="",mcap=5e7,liq=5e6,vol=1e7,tokens=12,ch=dict(m5=0.01,h1=0.08,h6=0.1,h24=0.2))]
fk.pairs={"ai-cats":[pair("MCAT","CATAI"),pair("MDOG","DOGAI",h1=0.02,v1=20000,v6=300000)]};fk.tok={"MCAT":pair("MCAT","CATAI"),"MDOG":pair("MDOG","DOGAI",h1=0.02,v1=20000,v6=300000)}
fk.trend=[dict(pool="P",mint="MCAT",symbol="CATAI",name="CATAI",price=1,vol={},ch={},liq=1,created=None,buys_h1=1,sells_h1=1,buyers_h1=1)]
def burst(n,base="the ai cats meta is heating up, $CATAI leads",weak=False,dup=False):
    W=["alpha","bravo","charlie","delta","echo","foxtrot","golf","hotel","india","juliet","kilo","lima","mike","november","oscar","papa","quebec","romeo","sierra","tango","uniform","victor","whiskey","xray","yankee","zulu"]
    fk.posts+=[post(("w" if weak else "a")+str(i)+str(NOW["t"]),base if dup else base+f" {W[i%26]} {W[(i*7+int(NOW['t']//600))%26]} {W[(i//26)%26]}",["CATAI"],followers=15 if weak else 30000,age=5 if weak else 1500) for i in range(n)]
# cycle 1: talk starts rising
burst(6);out=E.cycle(NOW["t"]);r1=[r for r in out if r["narrative"]=="ds:ai-cats"][0]
s1=E.social("CATAI",NOW["t"])
res["velocity vs its own 24h baseline, acceleration vs the hour before"]=s1["ratio"]>3 and s1["accel"]>1 and s1["accounts"]==6
res["Dexscreener meta + X talk = one named narrative"]=r1["name"]=="AI cats" and "x" in r1["families"] and "dexscreener" in r1["families"]
res["coin mapping: the coin really riding it (volume growth + price), alternatives listed"]=r1["ticker"]=="CATAI" and "DOGAI" in r1["evidence"]["7 why this coin"]
res["every score is logged, traded or not"]=db.q("select count(*) from narrative_scores")[0][0]>=1
# cycles 2-3: accelerating
for k,nn in((2,14),(3,30)):
    NOW["t"]+=900;burst(nn);out=E.cycle(NOW["t"])
T3=NOW["t"]
r3=[r for r in out if r["narrative"]=="ds:ai-cats"][0]
res["stage: emerging -> accelerating"]=r1["state"]=="emerging" and r3["state"]=="accelerating"
res["A+ only when every gate passes (score 85+)"]=r3["grade"]=="A+" and r3["score"]>=85 and not r3["gates"]
res["evidence answers the 7 questions"]=all(k in r3["evidence"] for k in("1 catalyst","2 sources","3 cause","4 direction","5 edge after costs","6 invalidation","7 why this coin"))
res["A+ handed over with the narrative id"]=r3["mint"]=="MCAT" and r3["narrative"]=="ds:ai-cats"
# sybil: same text from fresh tiny accounts
db2=DB(os.path.join(T,"n2.db"));fk2=F(db2);fk2.metas=fk.metas;fk2.pairs=fk.pairs;fk2.tok=fk.tok;fk2.trend=fk.trend;E2=NR.Engine(db2,cfg,None,feeds=fk2)
for h in range(1,25):E2.add_mention(NOW["t"]-h*3600-1800,"x:memes","x","CATAI","old%d"%h,1.0,5,f"cat {h}",NOW["t"])
fk=fk2
for k in range(3):
    burst(25,weak=True,dup=True);out2=E2.cycle(NOW["t"]+k*600)
rs=[r for r in out2 if r["narrative"]=="ds:ai-cats"][0]
res["sybil filter: copy-pasted posts from new tiny accounts never reach A+"]=rs["grade"]!="A+" and any("fake" in g for g in rs["gates"]) or (rs["grade"]!="A+" and rs["score"]<85)
# no social talk at all: market lists alone are not a narrative
db3=DB(os.path.join(T,"n3.db"));fk3=F(db3);fk3.metas=[dict(fk2.metas[0],ch=dict(m5=0.05,h1=0.6,h6=1,h24=2))];fk3.pairs=fk2.pairs;fk3.tok=fk2.tok;fk3.trend=fk2.trend;fk3.xon=False
E3=NR.Engine(db3,cfg,None,feeds=fk3)
for k in range(4):out3=E3.cycle(NOW["t"]+k*600)
r_=[r for r in out3 if r["narrative"]=="ds:ai-cats"][0]
res["NO NARRATIVE, NO TRADE: market heat without CT/news talk is blocked"]=r_["grade"]!="A+" and any("no CT/news talk" in g for g in r_["gates"])
# stages: decay after the peak
h=[[0,1],[1,4],[2,8],[3,3.9]];res["decaying: heat -50% from its peak"]=NR.stage(h,8,NR.DEF,0,10)=="decaying"
h=[[0,8],[1,7],[2,6],[3,5]];res["decaying: falling 3 checks in a row"]=NR.stage(h,8,NR.DEF,0,10)=="decaying"
res["peaking: flat near the peak"]=NR.stage([[0,5],[1,8],[2,7.9]],8,NR.DEF,0,10*3600)=="peaking"
# coin gates
pts=NR.coin_points(pair("M","X",h1=-0.02),NR.DEF,NOW["t"]);pts2=NR.coin_points(pair("M","X",liq=10000,age_h=0.2),NR.DEF,NOW["t"])
res["coin gates: price must confirm, liquidity, pair age"]=any("not confirming" in f for f in pts[2]) and any("liquidity" in f for f in pts2[2]) and any("min old" in f for f in pts2[2])
# majors for the perps book
E.add_mention(NOW["t"]-100,"news:coindesk","news","XRP","coindesk",2.0,0,"XRP surges after ETF approval",NOW["t"])
for i,w in enumerate(("approved","bullish breakout","rally","record inflows")):E.add_mention(NOW["t"]-50,"x:majors","x","XRP",f"m{i}",1.0,10,"XRP ETF "+w,NOW["t"])
E.flush();mj=E.major("XRP",NOW["t"])
res["perps: XRP narrative found with a bullish lean"]=mj["ok"] and mj["sentiment"]>0 and "news" in mj["families"]
res["perps: no talk -> no narrative"]=not E.major("ATOM",NOW["t"])["ok"]
# /narratives fits the phone
txt=NR.text(db);res["/narratives fits the phone"]=all(len(x)<=32 for x in plain(txt).split("\n")[1:]) and "A+" in plain(txt)
# ---- the rider buys the A+ through the whole paper gate, then the time-stop -------------------------------------------
c2=yaml.safe_load(open("config.yaml"));c2["paper"]["exec_latency_s"]=0;c2.setdefault("books",{})["narrative"]=dict(enabled=True,start_equity_usd=50,max_open_positions=3,max_position_pct=0.1,fee_pct=0.001,gas_usd=0.001,exec_latency_s=0)
LIVE={"px":1.0}
def q(inm,outm,raw,bps=300,detail=False):
    return (int(raw/LIVE["px"]),0.001,None) if inm==chain.USDC else (int(raw*LIVE["px"]*0.998),0.001,None)
paper.jup_quote=q;paper.decimals=lambda h,m:6;paper.jup_price=lambda m:(LIVE["px"],5e6);paper.jup_prices=lambda ms:{m:LIVE["px"] for m in ms}
paper.sol_usd=lambda:180.0;paper.storm=lambda d,c:None;veto.check=lambda *a,**k:(True,"ok",{})
FD.Feeds.atr_pct=lambda self,mint,minutes=15,n=14:0.06
class N:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):pass
book=paper.Paper(db,None,c2,N(),"narrative");rd=RD.Rider(db,book,c2)
db.kv_set("narr:queue",[dict(narrative=r3["narrative"],name=r3["name"],mint=r3["mint"],score=r3["score"],evidence=r3["evidence"],ts=T3)])
rd.tick(now=T3+60)
op=db.q("select id,meta,cost from b_positions where book='narrative' and status='open'")
m=json.loads(op[0][1]) if op else {}
res["rider: A+ bought through the full gate, narrative + score + plan stored"]=bool(op) and m.get("narrative")=="ds:ai-cats" and m.get("score")==r3["score"] and m["x"]["stop_pct"]==0.09
res["rider: size from 0.25% risk at the stop (fees included)"]=bool(op) and abs((op[0][2]-op[0][2]*0.001/1.001)-50*0.0025/(0.09+0.002))<0.02
res["traded flag set on the logged score"]=db.q("select count(*) from narrative_scores where traded=1")[0][0]==1
db.kv_set("narr:queue",[dict(narrative="ds:old",name="old",mint="MOLD",score=90,evidence={},ts=time.time()-3600)]);rd.tick()
res["rider: a stale A+ (graded long ago) is refused"]=db.q("select reason from b_signals where mint='MOLD'")[0][0].startswith("stale")
db.x("update b_positions set opened=? where book='narrative'",(time.time()-5*3600,));db.x("update narratives set state='decaying' where id='ds:ai-cats'")
rd.tick();res["time-stop: no first target in 4h and the narrative decays -> sold"]=db.q("select status from b_positions where book='narrative' and mint='MCAT'")[0][0]=="closed" and \
    db.q("select trigger from ledger where book='narrative' order by id desc limit 1")[0][0]=="time_stop"
db3=DB(os.path.join(T,"newpairs.db"));fk3=F(db3);fk3.newp=[dict(pool="P",mint="MNEW",symbol="NEWC",name="New coin",price=1.0,vol=dict(m5=1,h1=10,h6=10,h24=10),ch=dict(m5=0,h1=0,h6=0,h24=0),liq=60000,created=None)]
E3=NR.Engine(db3,cfg,None,feeds=fk3);E3.ingest_market(NOW["t"])
fam=db3.q("select family,weight from mentions where ticker='NEWC'")
res["new on-chain pairs are recorded as market evidence (family onchain), not as CT talk"]=fam==[("onchain",0.5)] and E3.social("NEWC",NOW["t"])["n"]==0
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
