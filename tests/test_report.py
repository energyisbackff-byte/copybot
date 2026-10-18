"""/report test on a made-up database (no network). Run: python3 tests/test_report.py"""
import sys,os,time,tempfile,re,html,json,csv,io
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
import yaml,report,notify
from db import DB
db=DB(tempfile.mktemp(suffix=".db"));cfg=yaml.safe_load(open("config.yaml"));res={}
t0=time.time()-5*86400;S="So11111111111111111111111111111111111111112";M="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
db.kv_set("copy:cash",50.1);db.kv_set("copy:last_equity",51.3);db.kv_set("trend:cash",30.0)
for i,(side,u,pnl) in enumerate([("buy",5.2,0),("sell",6.1,0.8),("buy",4.0,0),("sell",3.1,-0.95)]):
    db.ins("b_trades",ts=t0+i*3600,book="copy",trader="<b>whale</b>",mint=M,side=side,usd=u,tokens=1000,price=u/1000,pnl=pnl,sig=f"s{i}",note="impact 0.1%")
db.ins("b_positions",book="copy",trader="whale",mint=M,tokens=0,cost=0,opened=t0,status="closed",closed=t0+3600,realized=0.8)
db.ins("b_positions",book="copy",trader="whale",mint=M,tokens=0,cost=0,opened=t0+7200,status="closed",closed=t0+3*3600,realized=-0.95)
db.ins("b_fills",ts=t0,book="copy",sig="s0",side="buy",status="filled",fee_usd=0.11)
db.ins("b_signals",ts=t0,book="copy",source="whale",mint=M,side="buy",decision="refused",reason="safety veto")
db.c.execute("insert into trades values(?,?,?,?,?,?,?,?,?,?)",(t0-30*86400,"old",M,"sell",3.0,10,0.3,1.25,"x","old"));db.c.commit()
db.kv_set("backtest:v2",dict(version="trend-rider v2",passed=True,coins={"SOL":{"all":{"ret":0.259}}}))
db.kv_set("backtest:fast",dict(candidates=[dict(name="A dip-buyer",passed=False,coins={"SOL":{"all":{"ret":-0.248}}}),dict(name="B fast-trend",passed=False,coins={"SOL":{"all":{"ret":0.179}}})]))
msg=report.text(db,cfg);body=html.unescape(re.search(r"<pre>(.*?)</pre>",msg,re.S).group(1));print(body)
res["copy book: 2 buys, 2 sells, 1 won, 1 lost"]="buys   : 2  sells 2  open 0" in body and "1 trades +$0.80" in body and "1 trades -$0.95" in body
res["old copy bot history included"]="old copy bot" in body
res["backtests listed"]="v2: PASSED SOL +25.9%" in body and "dip-buyer: failed" in body
res["lines fit the phone"]=all(len(l)<=32 for l in body.split("\n"))
data,n=report.trades_csv(db);rows=list(csv.reader(io.StringIO(data.decode())))
res["CSV: header + 5 trades, oldest first"]=n==5 and len(rows)==6 and rows[1][1]=="copy-old"
res["CSV: profit only on sells"]=rows[2][7]=="" and rows[3][7]=="0.80"
cap={}
class FakeResp(io.BytesIO):pass
def fake_urlopen(req,timeout=None):cap["url"]=req.full_url;cap["body"]=req.data;return FakeResp(json.dumps({"ok":True}).encode())
notify.urllib.request.urlopen=fake_urlopen
ok=notify.send_document("123:abc","42","zetrax-trades.csv",data,"5 buys and sells")
res["file is uploaded to the chat"]=ok and cap["url"].endswith("/sendDocument") and b'filename="zetrax-trades.csv"' in cap["body"] and b"time_utc" in cap["body"] and b'name="chat_id"\r\n\r\n42' in cap["body"]
res["no token -> nothing sent, no crash"]=notify.send_document("","42","x.csv",b"a")is False
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
