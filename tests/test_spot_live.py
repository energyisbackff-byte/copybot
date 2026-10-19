"""Real money for the coin books (spot_live.py) against a fake coin guard, fake chain and fake Jupiter: no network, no key,
no money. Covers: off by default, the paper hook, /real on vs /real trial, approvals, sizing (share of the paper book,
caps), mirrored partial and full sells, sells that keep retrying and are never blocked (switched off, panic), stale and
cancelled buys, one coin per wallet, mismatch + clear, drawdown switch, restart, /real off all, /wallet link for both
Solana guards, the overview, and that main.py / notify.py / paper.py are wired. Run: python3 tests/test_spot_live.py"""
import sys,os,json,time,tempfile,re,html,hashlib
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(R,"signer"));sys.path.insert(0,R);os.chdir(R)
D=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_DB=os.path.join(D,"bot.db"),COPYBOT_NO_TELEGRAM="1",COPYBOT_MEMORY=os.path.join(D,"mem"))
import yaml,spot_live as SL,live as LV,solwire as W
from db import DB
from tgfmt import plain,W as WIDTH
cfg=yaml.safe_load(open("config.yaml"));db=DB(os.environ["COPYBOT_DB"]);db.kv_set("boot_id","boot1");res={}
COIN="CoinMint1111111111111111111111111111111111pump";COIN2="CoinMint2222222222222222222222222222222222pump"
HOME=bytes.fromhex("9b"*32)
while not W.on_curve(HOME):HOME=hashlib.sha256(HOME).digest()
HB=W.b58e(HOME);USDC=SL.USDC;CW=W.b58e(hashlib.sha256(b"coin wallet").digest());TW=W.b58e(hashlib.sha256(b"trend wallet").digest())
# ---- fake world: the coin wallet, prices, the guard ----------------------------------------------------------------------
CH=dict(usdc=30.0,sol=0.05,tok={},px={COIN:0.01,COIN2:0.02,SL.DEF["selftest_coin"]:0.00002},fail_sell=False,fail_buy=None,weird=False,txs={},n=0)
G=dict(home=HB,limits=dict(max_swap_usd=10.0,max_day_usd=50.0),calls=[])
T=dict(home=HB)          # the Trend Rider guard (only its home is read)
def fake_spot(op,timeout=40,**k):
    G["calls"].append(op)
    if op=="pubkey":return dict(wallet=CW)
    if op=="status":return dict(wallet=CW,home=G["home"],limits=G["limits"],today_usd=0,today_swaps=0,today_sells=0,mode="spot")
    if op=="check_static":
        if not str(k["tx"]).startswith("Q:"):raise LV.LiveError("refused: not a Jupiter swap")
        return dict(ok=True)
    if op in("check_swap","sign_swap"):
        q=json.loads(k["tx"][2:]);buy=q["inputMint"]==USDC
        if buy and CH["fail_buy"]:raise LV.LiveError(CH["fail_buy"])
        if not buy and CH["fail_sell"]:raise LV.LiveError("coin guard: simulation failed")
        if op=="check_swap":return dict(ok=True)
        CH["n"]+=1;sig=f"sig{CH['n']:04d}";CH["txs"][sig]=q;return dict(ok=True,tx=sig)
    if op=="set_home":G["home"]=k["address"];return dict(ok=True,home=k["address"])
    if op=="withdraw":return dict(ok=True,tx="w"+str(time.time()),amount=1000000,kind=k.get("kind"),home=G["home"],last_valid=10**9)
    raise LV.LiveError("unknown op")
_real_signer=LV.signer
def fake_signer(op,timeout=40,sock=None,**k):
    if sock==SL.SOCK:return fake_spot(op,timeout,**k)
    if op=="status":return dict(wallet=TW,home=T["home"],limits=dict(max_swap_usd=20,max_day_usd=100),today_usd=0,today_swaps=0,today_sells=0)
    if op=="set_home":T["home"]=k["address"];return dict(ok=True,home=k["address"])
    if op=="pubkey":return dict(wallet=TW)
    raise LV.LiveError("trend signer: "+op)
LV.signer=fake_signer;SL.signer=fake_spot
def quote(inm,outm,amt,bps):
    buy=inm==USDC;m=outm if buy else inm;px=CH["px"][m]
    out=int(amt/1e6/px*1e6*0.995) if buy else int(amt/1e6*px*1e6*0.995)
    return dict(inputMint=inm,outputMint=outm,inAmount=str(amt),outAmount=str(out),priceImpactPct="0.001")
SL.jup_quote_full=quote;SL.jup_swap_tx=lambda q,u,m:("Q:"+json.dumps(q),10**9);SL.tx_sig=lambda t:t;LV.tx_sig=lambda t:t;SL.decimals=lambda h,m:6
SL.jup_prices=lambda ms:{m:CH["px"].get(m) for m in ms}
class Hel:
    def rpc(s,m,p):
        if m=="getBalance":return {"value":int(CH["sol"]*1e9)}
        if m=="getTokenAccountsByOwner":
            if p[1].get("programId")!=SL.TOKEN:return {"value":[]}
            v=[(USDC,CH["usdc"])]+list(CH["tok"].items())
            return {"value":[{"account":{"data":{"parsed":{"info":{"mint":mt,"tokenAmount":{"uiAmountString":str(a)}}}}}} for mt,a in v]}
        if m=="sendTransaction":
            q=CH["txs"].get(p[0]);
            if q and not q.get("_done"):
                q["_done"]=1;buy=q["inputMint"]==USDC;mt=q["outputMint"] if buy else q["inputMint"]
                if buy:CH["usdc"]-=int(q["inAmount"])/1e6;CH["tok"][mt]=CH["tok"].get(mt,0)+(0 if CH["weird"] else int(q["outAmount"])/1e6)
                else:CH["tok"][mt]=CH["tok"].get(mt,0)-int(q["inAmount"])/1e6;CH["usdc"]+=int(q["outAmount"])/1e6
            return p[0]
        if m=="getSignatureStatuses":return {"value":[{"confirmationStatus":"confirmed","err":None}]}
        if m=="getBlockHeight":return 1
        if m=="getTransaction":
            q=CH["txs"].get(p[0])
            if not q:return None
            buy=q["inputMint"]==USDC;mt=q["outputMint"] if buy else q["inputMint"];w=CW
            du=-int(q["inAmount"]) if buy else int(q["outAmount"]);dt=(0 if CH["weird"] else int(q["outAmount"])) if buy else -int(q["inAmount"])
            pre=[{"owner":w,"mint":USDC,"uiTokenAmount":{"amount":"100000000","decimals":6}},{"owner":w,"mint":mt,"uiTokenAmount":{"amount":"100000000000","decimals":6}}]
            post=[{"owner":w,"mint":USDC,"uiTokenAmount":{"amount":str(100000000+du),"decimals":6}},{"owner":w,"mint":mt,"uiTokenAmount":{"amount":str(100000000000+dt),"decimals":6}}]
            return {"meta":{"preTokenBalances":pre,"postTokenBalances":post}}
        raise RuntimeError(m)
import chain;chain.Helius=Hel                     # every command reads the chain through the fake (no network)
SENT=[]
class N:
    token=""
    def send(s,t,buttons=None,tier="instant",digest=None):SENT.append((plain(t),buttons,tier))
S=SL.SpotLive(db,Hel(),N(),cfg)
def txt(t):return html.unescape(re.sub(r"<[^>]+>","",t))
def code_in(t,prefix):m=re.search(re.escape(prefix)+r" (\d{6})",txt(t));return m.group(1) if m else None
def paper_pos(book="narrative",mint=COIN):return db.ins("b_positions",book=book,trader="x",mint=mint,tokens=100,cost=5,opened=time.time(),status="open",open_sig="p",meta="{}")
def fits(t):return all(len(l)<=WIDTH for pre in re.findall(r"<pre>(.*?)</pre>",t,re.S) for l in html.unescape(pre).split("\n"))
# ---- off by default ---------------------------------------------------------------------------------------------------
p1=paper_pos();SL.on_paper(db,"narrative","buy",p1,COIN,size=5,equity=50)
res["off by default: a paper buy queues nothing"]=db.q("select count(*) from spot_intents")[0][0]==0
ov=SL.overview(db);res["/real lists every book OFF, fits the phone"]=all(x in txt(ov) for x in("Trend Rider","Narrative Rider","Trend basket","copy","consensus","perps")) and txt(ov).count("OFF")>=7 and fits(ov)
# ---- switching on --------------------------------------------------------------------------------------------------
t=S.arm("narrative");res["/real on before the self-test: refused with the reason"]="self-test" in txt(t) and "cannot turn on" in txt(t)
db.kv_set("live:test_withdraw",dict(sig="x",confirmed=True))
t=S.selftest();res["self-test: real Jupiter-shaped swap passes, the guard refuses the 3 bad transactions"]="coin wallet works" in txt(t) and txt(t).count("refuses")==3
t=S.arm("narrative");res["/real on with no paper record: not proven, offers trial"]="not proven" in txt(t) and "/real trial narrative" in txt(t).replace("\n  "," ")
t=S.arm("narrative",mode="trial");c=code_in(t,"/real trial narrative")
res["/real trial asks for a code and warns"]=bool(c) and "TRIAL" in txt(t) and fits(t)
res["wrong code refused"]="wrong" in txt(S.arm("narrative","000000","trial")) and not SL.book_on(db,"narrative")
t=S.arm("narrative",mode="trial");c=code_in(t,"/real trial narrative");t=S.arm("narrative",c,"trial")
res["trial ON after the code"]=bool(SL.book_on(db,"narrative")) and "REAL MONEY TRIAL" in txt(t)
res["other books stay OFF"]=not SL.book_on(db,"copy") and not SL.book_on(db,"trend-basket")
# ---- a mirrored buy ----------------------------------------------------------------------------------------------------
SENT.clear();SL.on_paper(db,"narrative","buy",p1,COIN,size=5,equity=50)
res["paper buy -> one real buy queued"]=db.q("select count(*) from spot_intents where kind='buy' and status='new'")[0][0]==1
S.tick();ap=db.kv_get("spot-live:approval")
res["trial: the buy waits for your tap (Approve / Reject buttons)"]=bool(ap) and any(b and "sappr:" in b[0][0][1] for _,b,_ in SENT)
res["size = the paper book's share (10%) of the real money: $3"]=abs(ap["size"]-3.0)<0.01
ap["status"]="yes";db.kv_set("spot-live:approval",ap);S.tick()
pos=S.positions();res["after the tap: bought, position linked to the paper position"]=len(pos)==1 and pos[0]["meta"]["paper_pid"]==p1 and abs(pos[0]["cost"]-3.0)<0.01 and "REAL BUY filled" in "".join(x for x,_,_ in SENT)
res["records: cash down by the cost"]=abs(db.kv_get("spot-live:cash")-27.0)<0.01
# ---- trial cap and the guard's cap -----------------------------------------------------------------------------------
p2=paper_pos(mint=COIN2);SL.on_paper(db,"narrative","buy",p2,COIN2,size=40,equity=50);S.tick();ap=db.kv_get("spot-live:approval")
res["trial: at most $5 a buy"]=ap and abs(ap["size"]-5.0)<0.01
ap["status"]="no";db.kv_set("spot-live:approval",ap);S.tick()
res["rejected: nothing bought"]=len(S.positions())==1 and db.q("select status from spot_intents where paper_pid=?",(p2,))[0][0]=="cancelled"
# ---- mirrored sells ----------------------------------------------------------------------------------------------------
tok0=S.positions()[0]["tokens"];SENT.clear()
SL.on_paper(db,"narrative","sell",p1,COIN,frac=0.5,reason="take_profit",note="first target")
S.tick();pos=S.positions()
res["paper sells 50% at the first target -> the real position sells 50%"]=len(pos)==1 and abs(pos[0]["tokens"]-tok0/2)<1e-6 and "REAL SELL" in "".join(x for x,_,_ in SENT)
CH["fail_sell"]=True;SENT.clear();SL.on_paper(db,"narrative","sell",p1,COIN,frac=1.0,reason="stop_loss",note="stop hit");S.tick()
it=db.q("select status,tries from spot_intents where kind='sell' order by id desc limit 1")[0]
res["a failed real sell is kept and retried (critical alert)"]=it==("retry",1) and any(t_=="critical" and "SELL failed" in x for x,_,t_ in SENT) and len(S.positions())==1
S.off("narrative");CH["fail_sell"]=False;S.tick(now=time.time()+30)
res["no retry storm: waits a minute"]=len(S.positions())==1
S.tick(now=time.time()+61)
res["switched OFF, the sell still goes through and closes the position"]=len(S.positions())==0 and db.q("select status from b_positions where book='spot-live' order by id desc limit 1")[0][0]=="closed"
pnl=db.q("select coalesce(sum(realized),0) from b_positions where book='spot-live'")[0][0];res["P&L recorded"]=abs(pnl)<1.0
# ---- buys that must not happen -------------------------------------------------------------------------------------------
def arm_now(book,mode="arm"):m=SL.arm_map(db);m[book]=dict(ts=time.time(),boot=db.kv_get("boot_id"),until=time.time()+86400,mode=mode);db.kv_set("spot-live:armed",m)
arm_now("copy");db.kv_set("spot-live:approved_count",10)
p3=paper_pos("copy");db.ins("spot_intents",ts=time.time()-300,book="copy",kind="buy",mint=COIN,paper_pid=p3,frac=0.1,reason="entry",note="",status="new");S.tick()
res["a buy older than 90 s (copy) is cancelled"]=db.q("select status,result from spot_intents order by id desc limit 1")[0]==("cancelled","too late")
p4=paper_pos("copy");SL.on_paper(db,"copy","buy",p4,COIN,size=5,equity=50);db.x("update b_positions set status='closed' where id=?",(p4,));S.tick()
res["the paper position closed before the real buy: cancelled"]=db.q("select status from spot_intents order by id desc limit 1")[0][0]=="cancelled"
p5=paper_pos("copy");SL.on_paper(db,"copy","buy",p5,COIN,size=5,equity=50);S.tick()
res["ON (approvals done): buys without a tap"]=len(S.positions())==1 and S.positions()[0]["book"]=="copy"
p6=paper_pos("consensus");arm_now("consensus");SL.on_paper(db,"consensus","buy",p6,COIN,size=5,equity=50);S.tick()
res["one coin per wallet: a second book can't buy the same coin"]=len(S.positions())==1 and "already holding" in db.q("select result from spot_intents order by id desc limit 1")[0][0]
p8=db.ins("b_positions",book="copy",trader="x",mint=COIN2,tokens=100,cost=1.8,opened=time.time(),status="open",open_sig="p",meta="{}")   # paper paid $0.018
SL.on_paper(db,"copy","buy",p8,COIN2,size=5,equity=50);S.tick()
res["the coin ran 11% above the paper buy: the real buy is skipped (stop/targets were planned from the paper price)"]=len(S.positions())==1 and "price ran" in db.q("select result from spot_intents order by id desc limit 1")[0][0]
db.x("update b_positions set status='closed' where id=?",(p8,))
CH["fail_buy"]="coin: holders can be frozen";p7=paper_pos("copy",COIN2);SENT.clear();SL.on_paper(db,"copy","buy",p7,COIN2,size=5,equity=50);S.tick()
res["the guard refuses a coin: nothing bought, you are told why"]=len(S.positions())==1 and any("frozen" in x for x,_,_ in SENT);CH["fail_buy"]=None
# ---- reconcile, drawdown, restart --------------------------------------------------------------------------------------
CH["usdc"]-=5;SENT.clear();S.reconcile()
res["wallet USDC below the records: mismatch, every coin book OFF, critical alert"]=bool(db.kv_get("spot-live:mismatch")) and not SL.arm_map(db) and any(t_=="critical" for _,_,t_ in SENT)
t=S.clear();c=code_in(t,"/real clear");S.clear(c);res["/real clear with the code resets the records"]=not db.kv_get("spot-live:mismatch") and abs(db.kv_get("spot-live:cash")-CH["usdc"])<0.01
arm_now("copy");db.kv_set("spot-live:peak",1000.0);S.mark()
res["drawdown switch: 10% under the peak -> OFF"]=bool(db.kv_get("spot-live:shutdown")) and not SL.arm_map(db)
db.kv_set("spot-live:shutdown",None);arm_now("copy");db.kv_set("boot_id","boot2")
res["a restart switches it off (new boot id)"]=SL.book_on(db,"copy") is None
src=open("main.py").read()
res["main.py: restart clears the switches, the worker thread starts, /real routed"]=all(x in src for x in('db.kv_set("spot-live:armed",None)',"_spot.runner_thread(DBP,cfg,n)",'_sl.cmd(cdb,text,cfg,n)'))
# ---- /real off all, /panic ----------------------------------------------------------------------------------------------
db.kv_set("boot_id","boot1");arm_now("copy");db.kv_set("live:armed",{"x":1});db.kv_set("perps-live:armed",{"x":1})
t=SL.cmd(db,"/real off all",cfg,N())
res["/real off all: Trend Rider, perps and the coin books all OFF"]=not db.kv_get("live:armed") and not db.kv_get("perps-live:armed") and not SL.arm_map(db) and "ALL real money is OFF" in txt(t)
import main
SENT.clear();t=main.live_cmd("/panic",db,"/panic",N())
res["/panic: coin wallet OFF and selling"]="coin wallet: OFF, selling 1" in txt(t) and db.kv_get("spot-live:panic_sell")
S.tick();S.tick()
res["/panic sells the real coin position, then reports flat"]=len(S.positions())==0 and not db.kv_get("spot-live:panic_sell")
main.live_cmd("/panic",db,"/panic off",N())
# ---- delegation to the other switches -----------------------------------------------------------------------------------
t=SL.cmd(db,"/real on trend",cfg,N());res["/real on trend uses the Trend Rider checklist"]="live --arm" in txt(t) or "SAFETY" in txt(t)
t=SL.cmd(db,"/real trial perps",cfg,N());res["/real trial perps uses the perps checklist"]="perps live" in txt(t)
t=SL.cmd(db,"/real on basket",cfg,N());res["/real on basket = the Trend basket book"]="Trend basket" in txt(t)
res["/real on with no book asks which"]="name the book" in txt(SL.cmd(db,"/real on",cfg,N()))
# ---- /wallet link: one command links both Solana wallets ------------------------------------------------------------------
G["home"]=None;T["home"]=None;db.kv_set("live:home_pending",None)
NEW=HOME
t=main.live_cmd("/wallet",db,"/wallet link "+W.b58e(NEW),N());c=code_in(t,"/sethome confirm")
res["/wallet link ADDRESS shows it for checking, asks for a code"]=bool(c) and "EVERY character" in txt(t)
t=main.live_cmd("/sethome",db,f"/sethome confirm {c}",N())
res["confirm links BOTH Solana wallets to your Phantom"]=G["home"]==W.b58e(NEW) and T["home"]==W.b58e(NEW) and "Phantom linked" in txt(t) and fits(t)
t=main.live_cmd("/wallet",db,"/wallet",N())
res["/wallet shows all three wallets and how to link"]=all(x in txt(t) for x in("Trend Rider","coin books","Hyperliquid","/wallet link")) and fits(t)
# ---- the other wiring ---------------------------------------------------------------------------------------------------
res["notify.py: Approve/Reject taps for the coin books (sappr)"]='"sappr"' in open("notify.py").read() and "spot-live:approval" in open("notify.py").read()
ps=open("paper.py").read()
res["paper.py: every paper buy and sell (incl. write-off) tells the real book"]=ps.count("spot_live.on_paper(")==3
res["config: spot_live block (trial $5, 7 days, 10 approvals)"]=cfg["spot_live"]["trial_max_usd"]==5 and cfg["spot_live"]["arm_days"]==7 and cfg["spot_live"]["approvals_required"]==10
import tgfmt;res["Telegram menu has /real"]=any(c_=="real" for c_,_ in tgfmt.COMMANDS)
long=[l for x,_,_ in SENT for l in x.split("\n")[1:] if len(l)>WIDTH and not l.startswith("root@") and " " in l.strip()]   # a full address line (tap to copy) may be long
res["every message fits the phone"]=not long
if long:print(long[:5])
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
