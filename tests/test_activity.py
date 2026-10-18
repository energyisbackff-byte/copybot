"""Activity stream for the dashboard live scene (no network). Run: python3 tests/test_activity.py"""
import sys,os,json,tempfile,importlib
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,R);os.chdir(R)
from db import DB;import activity
d=tempfile.mkdtemp();p=os.path.join(d,"bot.db");db=DB(p)
activity.act(db,"tx",trader="<b>x</b>",side="buy",mint="M"*44,usd=1.5,detect_s=None)
activity.act(db,"skip",book="copy",trader="t",mint="M",reason="liquidity $1 < $50,000",checks=[["liquidity","$1",False]])
for i in range(450):activity.act(db,"ws",up=bool(i%2))
assert db.q("select count(*) from activity")[0][0]<=3200
class Broken: c=None
activity.act(Broken(),"tx")            # must never raise
open(os.path.join(d,"token.txt"),"w").write("tok");open(os.path.join(d,"heartbeat"),"w").write("1")
os.environ.update(COPYBOT_DB=p,COPYBOT_HEARTBEAT=os.path.join(d,"heartbeat"),CB_STATE=d)
sys.path.insert(0,os.path.join(R,"dashboard"))
import shutil;shutil.copy(os.path.join(R,"dashboard","server.py"),os.path.join(d,"server.py"));shutil.copy(os.path.join(R,"dashboard","index.html"),os.path.join(d,"index.html"))
sys.path.insert(0,d);srv=importlib.import_module("server")
a=srv.activity(0);json.dumps(a,allow_nan=False)
assert a["events"] and a["events"][-1]["kind"]=="ws" and "watch" in a and "cards" in a and a["heartbeat_age"] is not None
last=a["events"][-1]["id"];assert srv.activity(last)["events"]==[]
print("activity: OK")
