"""End-to-end test of the real-money path with the REAL signer code, against a fake chain + fake Jupiter (no network,
no real key, no money). Covers: home confirm, test withdrawal, self-test (incl. the signer's refusals), the readiness
policy, arm vs trial, approvals, caps, exits after the buy limit is used up, panic retries, a transaction whose outcome
was unknown (recovered without a double sell), expiry, mismatch + clear, and withdrawals.  Run: python3 tests/test_live.py"""
import sys,os,json,base64,struct,threading,time,tempfile,socket,hashlib,re,html,types
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(R,"signer"));sys.path.insert(0,R);os.chdir(R)
D=tempfile.mkdtemp();PORT=18998
os.environ.update(SIGNER_STATE=D,SIGNER_SOCK=os.path.join(D,"s.sock"),SIGNER_CONF=os.path.join(D,"conf.json"),SIGNER_RPC=f"http://127.0.0.1:{PORT}/rpc",
    JUP_BASE=f"http://127.0.0.1:{PORT}",SIGNER_CLIENT="root",HELIUS_API_KEY="x",COPYBOT_DB=os.path.join(D,"bot.db"),COPYBOT_NO_TELEGRAM="1")
import solwire as W,signer as SG
kp=SG.keypair();WALLET=kp[32:];WB=W.b58e(WALLET)
A=lambda t:hashlib.sha256(t.encode()).digest();USDC_ACC=W.ata(WALLET,W.USDC);WSOL_ACC=W.ata(WALLET,W.WSOL)
HOME=bytes.fromhex("9b"*32)
while not W.on_curve(HOME):HOME=hashlib.sha256(HOME).digest()
CH=dict(sol=50_000_000,usdc=0,px=150.0,landed={},held={},hold=False,jup_down=False,bh=100,lv=10**9,home_usdc=0)
def tok_data(amount,owner=WALLET):return W.USDC+owner+struct.pack("<Q",amount)+struct.pack("<I",0)+b"\0"*32+bytes([1])+b"\0"*56
def effect(raw):
    """What a tx does to the wallet: our marker in the fake Jupiter instruction, or a transfer home."""
    t=W.Tx(raw);ds=du=0;home_usdc=0
    for ix in t.ix:
        p=t.program(ix)
        if p==W.JUPITER:
            d,amt=struct.unpack("<BQ",ix["data"][:9])
            if d==1:du=-amt;ds=int(amt/1e6/CH["px"]*1e9*0.998)
            else:ds=-amt;du=int(amt/1e9*CH["px"]*1e6*0.998)
        if p==W.SYSTEM and t.keys[ix["accounts"][1]]==HOME:ds-=struct.unpack("<IQ",ix["data"])[1]
        if p==W.ATA:ds-=2039280                                   # rent for home's new USDC account
        if p==W.TOKEN and ix["data"][0]==12:a=struct.unpack("<Q",ix["data"][1:9])[0];du-=a;home_usdc+=a
    return ds-5000,du,home_usdc
def view(a,sol,usdc):
    if a==WB:return {"lamports":sol,"data":["","base64"],"owner":"x"}
    if a==W.b58e(USDC_ACC):return {"lamports":2039280,"data":[base64.b64encode(tok_data(usdc)).decode(),"base64"],"owner":"x"}
    return None
class H(BaseHTTPRequestHandler):
    def log_message(s,*a):pass
    def reply(s,obj,code=200):b=json.dumps(obj).encode();s.send_response(code);s.end_headers();s.wfile.write(b)
    def do_GET(s):
        if s.path.startswith("/price/v3"):return s.reply({W.b58e(W.WSOL):{"usdPrice":CH["px"],"liquidity":5e8}})
        if s.path.startswith("/swap/v1/quote"):
            if CH["jup_down"]:return s.reply({"error":"down"},503)
            from urllib.parse import urlparse,parse_qs;q={k:v[0] for k,v in parse_qs(urlparse(s.path).query).items()}
            amt=int(q["amount"]);buy=q["inputMint"]==W.b58e(W.USDC)
            out=int(amt/1e6/CH["px"]*1e9) if buy else int(amt/1e9*CH["px"]*1e6)
            return s.reply(dict(inputMint=q["inputMint"],outputMint=q["outputMint"],inAmount=str(amt),outAmount=str(out),priceImpactPct="0.0004",slippageBps=int(q["slippageBps"])))
    def do_POST(s):
        body=json.loads(s.rfile.read(int(s.headers["content-length"])))
        if s.path=="/swap/v1/swap":
            q=body["quoteResponse"];buy=q["inputMint"]==W.b58e(W.USDC);user=W.pk(body["userPublicKey"])
            keys=[user,WSOL_ACC,USDC_ACC,W.WSOL,W.USDC,W.COMPUTE,W.ATA,W.SYSTEM,W.TOKEN,W.JUPITER]
            ix=[(5,[],bytes([2])+struct.pack("<I",400000)),(5,[],bytes([3])+struct.pack("<Q",100000)),(6,[0,1,0,3,7,8],b"\x01"),
                (9,[0,1,2],struct.pack("<BQ",1 if buy else 2,int(q["inAmount"]))+bytes(20)),(8,[1,0,0],bytes([9]))]
            m=bytes([0x80,1,0,6])+W.cu16(len(keys))+b"".join(keys)+A("bh"+str(time.time_ns()))+W.cu16(len(ix))
            for p,a,d in ix:m+=bytes([p])+W.cu16(len(a))+bytes(a)+W.cu16(len(d))+d
            m+=W.cu16(0);raw=W.cu16(1)+b"\0"*64+m
            return s.reply(dict(swapTransaction=base64.b64encode(raw).decode(),lastValidBlockHeight=CH["lv"]))
        m=body["method"];p=body["params"]
        if m=="getTokenAccountsByOwner":
            mine=p[0]==WB and p[1]["mint"]==W.b58e(W.USDC)
            v=[{"pubkey":W.b58e(USDC_ACC),"account":{"data":{"parsed":{"info":{"tokenAmount":{"uiAmountString":str(CH["usdc"]/1e6)}}}}}}] if mine else []
            res={"value":v}
        elif m=="getBalance":res={"value":CH["sol"]}
        elif m=="getMultipleAccounts":res={"value":[view(a,CH["sol"],CH["usdc"]) for a in p[0]]}
        elif m=="simulateTransaction":
            ds,du,_=effect(base64.b64decode(p[0]))
            if CH["usdc"]+du<0 or CH["sol"]+ds<0:res={"value":{"err":{"InstructionError":[2,{"Custom":1}]},"accounts":None}}
            else:res={"value":{"err":None,"accounts":[view(a,CH["sol"]+ds,CH["usdc"]+du) for a in p[1]["accounts"]["addresses"]]}}
        elif m=="sendTransaction":
            raw=base64.b64decode(p[0]);t=W.Tx(raw);sig=W.b58e(raw[1:65])
            assert W.verify(WALLET,raw[1:65],t.msg),"bad signature reached the chain"
            if CH["hold"]:CH["held"][sig]=raw
            elif sig not in CH["landed"]:land(sig,raw)
            res=sig
        elif m=="getSignatureStatuses":res={"value":[{"confirmationStatus":"confirmed","err":None} if x in CH["landed"] else None for x in p[0]]}
        elif m=="getBlockHeight":res=CH["bh"]
        elif m=="getTransaction":
            x=CH["landed"].get(p[0])
            res=None if not x else {"meta":{"preBalances":[x[0]],"postBalances":[x[1]],"preTokenBalances":[{"owner":WB,"mint":W.b58e(W.USDC),"uiTokenAmount":{"amount":str(x[2])}}],
                "postTokenBalances":[{"owner":WB,"mint":W.b58e(W.USDC),"uiTokenAmount":{"amount":str(x[3])}}]},"transaction":{"message":{"accountKeys":[{"pubkey":WB}]}}}
        elif m=="getLatestBlockhash":res={"value":{"blockhash":W.b58e(A("bh"+str(time.time_ns()))),"lastValidBlockHeight":CH["lv"]}}
        else:res=None
        s.reply({"jsonrpc":"2.0","id":1,"result":res})
def land(sig,raw):
    ds,du,hu=effect(raw);pre=(CH["sol"],CH["usdc"]);CH["sol"]+=ds;CH["usdc"]+=du;CH["home_usdc"]+=hu
    CH["landed"][sig]=(pre[0],CH["sol"],pre[1],CH["usdc"])
srv=ThreadingHTTPServer(("127.0.0.1",PORT),H);threading.Thread(target=srv.serve_forever,daemon=True).start()
threading.Thread(target=SG.serve,daemon=True).start();time.sleep(0.4)
import chain;chain.JUP=f"http://127.0.0.1:{PORT}"
import live as L;L.JUP=chain.JUP;L.SOCK=os.environ["SIGNER_SOCK"]
CLK=[time.time()]
L.time=types.SimpleNamespace(time=lambda:CLK[0],sleep=lambda x:CLK.__setitem__(0,CLK[0]+x),strftime=time.strftime,gmtime=time.gmtime,localtime=time.localtime)
class Hel:
    def rpc(s,m,p):
        import urllib.request;r=json.load(urllib.request.urlopen(urllib.request.Request(os.environ["SIGNER_RPC"],json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),{"content-type":"application/json"}),timeout=10))
        return r["result"]
L.jup_price=lambda m:(CH["px"],5e8)
from db import DB;import yaml
db=DB(os.environ["COPYBOT_DB"]);cfg=yaml.safe_load(open(os.path.join(R,"config.yaml")));SENT=[]
class N:
    token=""
    def send(s,t,buttons=None,**k):SENT.append((t,buttons))
def txt(t):return html.unescape(re.sub(r"<[^>]+>","",t))
db.kv_set("boot_id","boot1");lv=L.Live(db,Hel(),N(),cfg);res={}
import main;main.Helius=Hel;chain.Helius=Hel;main.time=L.time
def cmd(c):
    out=main.live_cmd(c.split()[0],db,c,N());RAW.append(out);return txt(out)
RAW=[]
def code_in(t,word):m=re.search(word+r" (\d{6})",t);return m.group(1) if m else None
# ---- unfunded wallet: nothing can turn on, the self-test still proves the chain and the refusals
res["unfunded: /live arm refused"]="cannot" in cmd("/live arm")
res["unfunded: /live trial refused"]="cannot" in cmd("/live trial")
t=cmd("/live selftest");print(t)
res["self-test: real tx shapes pass, signer refuses 3 bad txs, no money moved"]=t.count("refuses ")==3 and "instruction checks" in t and not CH["landed"] and "ACCEPTED" not in t
res["self-test unfunded is not the full test"]=not (db.kv_get("live:selftest") or {}).get("full")
# ---- home address: check + confirm code
t=cmd("/sethome "+W.b58e(W.ata(HOME,W.USDC)))     # a token account address: a common copy mistake
res["sethome refuses a token-account (program) address"]="not a wallet" in t
t=cmd("/sethome "+W.b58e(HOME));c=code_in(t,"/sethome confirm")
res["sethome shows the address and asks for a code"]=bool(c) and "EVERY character" in t
res["wrong confirm code refused"]="wrong" in cmd("/sethome confirm 000000")
t=cmd("/sethome "+W.b58e(HOME));c=code_in(t,"/sethome confirm");t=cmd(f"/sethome confirm {c}")
res["home set after confirm"]="home address set" in t and SG.state().get("home")==W.b58e(HOME)
# ---- fund: test withdrawal, received, full self-test
CH["usdc"]=30_000_000;CH["sol"]=40_000_000
t=cmd("/live testwithdraw");c=code_in(t,"/live testwithdraw");cmd(f"/live testwithdraw {c}");lv.tick()
res["test withdrawal: $1 USDC to home (account created)"]=CH["home_usdc"]==1_000_000 and "test withdrawal sent" in SENT[-1][0]
res["safety check waits for your confirmation"]=not any(ok for ok,t_ in lv.checks()["safety"] if "test withdrawal" in t_)
cmd("/live received");res["after /live received the check passes"]=any(ok for ok,t_ in lv.checks()["safety"] if "test withdrawal" in t_)
t=cmd("/live selftest");res["funded self-test is the full test"]=(db.kv_get("live:selftest") or {}).get("full") is True and "simulation ok" in t
ch=lv.checks();print("\n".join(lv.checklist_rows(ch)))
res["all SAFETY checks pass"]=all(ok for ok,_ in ch["safety"])
res["STRATEGY checks fail (no v2 backtest yet)"]=not all(ok for ok,_ in ch["strategy"])
# ---- arm needs the strategy; trial does not
t=cmd("/live arm");res["/live arm refused: strategy not proven"]="cannot arm" in t and "/live trial" in t
t=cmd("/live trial");c=code_in(t,"/live trial");res["/live trial warns and asks for a code"]=bool(c) and "NOT" in t and "proven" in t
t=cmd(f"/live trial {c}");res["trial switched on"]=lv.mode()=="trial" and "TRIAL" in t
meta=dict(v=2,stop=140.0,hi=150.0,signal_ref=150.0,signal_ts=0,features=dict(close=150.0,stop_pct=0.067))
db.kv_set("live:approved_count",10)
out=lv.propose(25.0,meta,"t1");a=db.kv_get("live:approval")
res["trial: every buy needs a tap (even after 10)"]=out=="pending" and SENT[-1][1] and "appr:" in SENT[-1][1][0][0][1]
res["trial: size capped at $10"]=a and abs(a["size"]-10.0)<1e-9
a["status"]="yes";db.kv_set("live:approval",a);u0=CH["usdc"];lv.process_approval(150.0,lambda px,m:m["stop"]<px<=m["signal_ref"]*1.05)
pos=lv.position();res["approved trial buy: $10 on chain, exact amounts recorded"]=pos and CH["usdc"]==u0-10_000_000 and abs(pos["cost"]-10.0)<1e-9 and pos["meta"]["stop"]==140.0
# ---- exits are never blocked by the signer's buy limits
SG.state(dict(day=time.strftime("%Y-%m-%d",time.gmtime()),day_usd=100.0,day_swaps=10,day_sells=0,day_fee=0))
u1=CH["usdc"];ok=lv.exit(pos["id"],"stop_loss","stop $140 hit")
res["exit works after the daily buy limit is used up"]=ok and lv.position() is None and CH["usdc"]>u1
res["a buy is refused by the signer at the limit"]=lv.buy(5.0,meta,"t2") is None and lv.position() is None
SG.state(dict(day="2000-01-01"))
# ---- a sell whose outcome is unknown: kept, recovered later, never sold twice
db.kv_set("live:approved_count",10);db.kv_set("live:armed",dict(db.kv_get("live:armed"),mode="arm"))
pid=lv.buy(10.0,meta,"t3");res["armed + 10 approvals: direct buy"]=isinstance(pid,int)
CH["hold"]=True;lv.c["max_send_seconds"]=20;n_land=len(CH["landed"])
ok=lv.exit(pid,"stop_loss","stop hit");p=db.kv_get("live:pending_tx")
res["unknown outcome: position kept, tx remembered"]=not ok and lv.position() is not None and p and p["kind"]=="sell"
res["while a tx is open, new buys are blocked"]=lv.blocked()=="a transaction is still being confirmed"
CLK[0]+=61;ok2=lv.exit(pid,"stop_loss","stop hit")
res["retry re-sends the SAME tx (no second sell)"]=len(CH["held"])==1 and not ok2
CH["hold"]=False;sig=list(CH["held"])[0];land(sig,CH["held"].pop(sig))     # it lands later
CLK[0]+=31;lv.tick()
res["pending sell recovered: position closed once"]=lv.position() is None and db.kv_get("live:pending_tx") is None and len(CH["landed"])==n_land+1
res["recovery is reported"]=any("recovered" in x[0] for x in SENT[-3:])
# expired tx: cleared, nothing changed, a NEW sell is allowed
pid=lv.buy(10.0,meta,"t4");CH["hold"]=True;lv.exit(pid,"stop_loss","x");CH["hold"]=False;CH["bh"]=CH["lv"]+1;CH["held"].clear()
r=lv.resolve_pending();res["expired tx cleared (nothing landed)"]=r=="expired" and db.kv_get("live:pending_tx") is None and lv.position() is not None
CH["bh"]=100;CLK[0]+=61;res["after expiry a new sell goes through"]=lv.exit(pid,"stop_loss","x") and lv.position() is None
# ---- panic: retries until sold, never re-arms
pid=lv.buy(10.0,meta,"t5");CH["jup_down"]=True
cmd("/panic");res["panic switches live off"]=not lv.armed() and db.kv_get("live:panic_sell")
lv.tick();res["panic sell failing: position kept, flag kept"]=lv.position() is not None and db.kv_get("live:panic_sell")
CH["jup_down"]=False;CLK[0]+=30;lv.tick();res["retry waits a minute"]=lv.position() is not None
CLK[0]+=31;lv.tick();res["panic sell done on retry"]=lv.position() is None and not db.kv_get("live:panic_sell")
cmd("/panic off");res["/panic off does not turn live back on"]=not lv.armed() and not db.kv_get("panic")
# ---- arming expires after 7 days
db.kv_set("live:armed",dict(ts=CLK[0],boot="boot1",until=CLK[0]+7*86400,mode="arm"))
CLK[0]+=6*86400+60;lv.tick();res["warned 24h before expiry"]=any("expires soon" in x[0] for x in SENT[-2:])
CLK[0]+=86400;lv.tick();res["expired after 7 days"]=not lv.armed() and db.kv_get("live:armed") is None
# ---- wallet mismatch: off, flag blocks re-arming, /live clear with code
db.kv_set("live:armed",dict(ts=CLK[0],boot="boot1",until=CLK[0]+86400,mode="arm"));lv.kset("cash",CH["usdc"]/1e6);CH["usdc"]-=5_000_000
lv.reconcile();res["mismatch: live off + alert"]=not lv.armed() and "MISMATCH" in SENT[-1][0]
res["mismatch blocks turning on"]=not all(ok for ok,_ in lv.checks()["safety"])
t=cmd("/live clear");c=code_in(t,"/live clear");t=cmd(f"/live clear {c}")
res["/live clear resets records to the wallet"]="cleared" in t and db.kv_get("live:mismatch") is None and abs(lv.kget("cash")-CH["usdc"]/1e6)<1e-6
# ---- the normal switch-on once the strategy is proven (backtest passed + 14 paper days)
db.kv_set("backtest:v2",dict(version="trend-rider v2",passed=True,checks=[],bar=dict(passed=True,checks=[])));db.kv_set("trend:v2_started",CLK[0]-13*86400)
t13=cmd("/live arm");res["arm refused with only 13 paper days (needs 14)"]="paper 13.0 days" in t13 and not lv.armed()
db.kv_set("trend:v2_started",CLK[0]-15*86400)
cmd("/live selftest");t=cmd("/live arm");c=code_in(t,"/live arm");t=cmd(f"/live arm {c}")
res["normal arm when every check passes"]=lv.mode()=="arm" and "ARMED" in t
db.kv_set("live:approved_count",3);res["armed: the first 10 buys still need a tap"]=lv.propose(30.0,meta,"t9")=="pending"
db.kv_set("live:approval",None);cmd("/live off");res["/live off"]=not lv.armed()
# ---- withdraw everything (USDC goes straight to home's USDC account)
t=cmd("/withdraw");c=code_in(t,"/withdraw");cmd(f"/withdraw {c}");lv.tick()
res["withdraw: all USDC and SOL home"]=CH["usdc"]==0 and CH["sol"]<=5000 and "withdrawn" in SENT[-1][0]
# ---- restart switches live off
db.kv_set("live:armed",dict(ts=CLK[0],boot="boot1",until=CLK[0]+86400,mode="arm"));db.kv_set("boot_id","boot2")
res["a restart switches live off"]=not lv.armed()
# ---- screens fit the phone
for c_ in ["/live","/wallet","/live arm","/live trial","/live off","/live backedup","/live bogus"]:cmd(c_)
long=[l for t,_ in SENT for pre in re.findall(r"<pre>(.*?)</pre>",t,re.S) for l in html.unescape(pre).split("\n") if len(l)>32]
long+=[l for t in RAW for pre in re.findall(r"<pre>(.*?)</pre>",t,re.S) for l in html.unescape(pre).split("\n") if len(l)>32]
res["every message fits 32 columns"]=not long
if long:print("long lines:",long[:6])
print(txt(main.live_cmd("/live",db,"/live",N())))
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");os._exit(1 if bad else 0)
