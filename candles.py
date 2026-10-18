"""Daily candles for Trend Rider v2 (SOL, BTC, ETH in USD). Coinbase Exchange public API (no key), with Kraken as a
fallback for recent candles. Stored in the `candles` table (sym, tf, ts) so the backtest and the live strategy read
exactly the same data. Only CLOSED candles are ever used: a candle starting at ts is closed once ts+tf <= now.
Daily candles are UTC days: the candle starting 00:00 UTC closes at the next 00:00 UTC."""
import json,time,logging,urllib.request,datetime as dt
log=logging.getLogger("bot")
DAY=86400
COINS={"SOL":"SOL-USD","BTC":"BTC-USD","ETH":"ETH-USD"}
CB="https://api.exchange.coinbase.com/products/{sym}/candles"
KR={"SOL-USD":"SOLUSD","BTC-USD":"XBTUSD","ETH-USD":"ETHUSD"}
HISTORY_FROM=int(dt.datetime(2021,6,1,tzinfo=dt.timezone.utc).timestamp())   # SOL-USD trades on Coinbase from June 2021
def _get(u,timeout=20):
    return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"user-agent":"copybot-trend","accept":"application/json"}),timeout=timeout))
def _iso(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def fetch_coinbase(sym,tf,start,end,tries=4,timeout=20):
    """Candles starting in [start, end), oldest first: (ts, open, high, low, close, volume). At most 300 per call."""
    out=[];t=start
    while t<end:
        e=min(end,t+300*tf)
        for a in range(tries):
            try:rows=_get(CB.format(sym=sym)+f"?granularity={tf}&start={_iso(t)}&end={_iso(e)}",timeout);break
            except Exception:
                if a==tries-1:raise
                time.sleep(1.5*(a+1))
        out+=[(int(r[0]),float(r[3]),float(r[2]),float(r[1]),float(r[4]),float(r[5])) for r in rows if start<=int(r[0])<end]
        t=e;time.sleep(0.25)
    return sorted(set(out))
def fetch_kraken(sym,tf):
    r=_get(f"https://api.kraken.com/0/public/OHLC?pair={KR[sym]}&interval={tf//60}",10)
    if r.get("error"):raise RuntimeError(str(r["error"]))
    rows=next(v for k,v in r["result"].items() if k!="last")
    return [(int(x[0]),float(x[1]),float(x[2]),float(x[3]),float(x[4]),float(x[6])) for x in rows]
def store(db,sym,tf,rows):
    db.c.executemany("insert or replace into candles(sym,tf,ts,o,h,l,c,v) values(?,?,?,?,?,?,?,?)",[(sym,tf)+tuple(r) for r in rows]);db.c.commit()
def last_ts(db,sym="SOL-USD",tf=DAY):return db.q("select max(ts) from candles where sym=? and tf=?",(sym,tf))[0][0]
def sync(db,sym="SOL-USD",tf=DAY,need_days=150,now=None):
    """Bring stored candles up to the last CLOSED candle. Returns the newest closed candle start time (or None)."""
    now=now or time.time();closed_end=int(now//tf*tf)          # candles starting before this are closed
    lt=last_ts(db,sym,tf);start=max(lt+tf if lt else 0,closed_end-need_days*DAY)
    if start>=closed_end:return lt
    try:rows=fetch_coinbase(sym,tf,start,closed_end,tries=1,timeout=8)   # live path: fail fast, the main loop must not stall
    except Exception as e:
        log.warning("coinbase candles failed (%s), trying kraken",e)
        rows=[r for r in fetch_kraken(sym,tf) if start<=r[0]<closed_end]
    store(db,sym,tf,rows);return last_ts(db,sym,tf)
def backfill(db,sym,tf=DAY,start=HISTORY_FROM,progress=None):
    """Fill history for the backtest; skips what is already stored. Returns the number of stored closed candles."""
    end=int(time.time()//tf*tf);start=int(start//tf*tf)
    have={t for (t,) in db.q("select ts from candles where sym=? and tf=? and ts>=?",(sym,tf,start))}
    t=start;chunk=300*tf
    while t<end:
        e=min(end,t+chunk);need=(e-t)//tf
        if sum(1 for x in range(t,e,tf) if x in have)<need*0.98:
            store(db,sym,tf,fetch_coinbase(sym,tf,t,e))
            if progress:progress((e-start)/(end-start))
        t=e
    return db.q("select count(*) from candles where sym=? and tf=? and ts>=? and ts<?",(sym,tf,start,end))[0][0]
def load(db,sym="SOL-USD",tf=DAY,since=0,until=None):
    until=until or int(time.time()//tf*tf)
    return db.q("select ts,o,h,l,c,v from candles where sym=? and tf=? and ts>=? and ts<? order by ts",(sym,tf,since,until))
