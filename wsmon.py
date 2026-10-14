"""Real-time wallet watcher: standard Solana logsSubscribe (mentions filter) over Helius WebSocket.
Works on the Helius free plan (standard WS methods). Puts (wallet, signature, detect_ts) on a queue.
Auto-reconnects with backoff and resubscribes when the watched set changes."""
import asyncio,json,os,threading,time,logging,queue
log=logging.getLogger("bot")
class WalletStream(threading.Thread):
    def __init__(s,commitment="confirmed"):
        super().__init__(daemon=True);s.q=queue.Queue();s.wallets=set();s.lock=threading.Lock();s.ver=0
        s.url="wss://mainnet.helius-rpc.com/?api-key="+os.environ["HELIUS_API_KEY"].strip();s.commitment=commitment
        s.connected=False;s.last_msg=0;s.notifications=0;s.reconnects=0
    def set_wallets(s,ws):
        ws=set(ws)
        with s.lock:
            if ws!=s.wallets: s.wallets=ws;s.ver+=1
    def run(s): asyncio.run(s.main())
    async def main(s):
        import websockets
        back=1
        while True:
            try:
                with s.lock: wallets=list(s.wallets);ver=s.ver
                if not wallets: await asyncio.sleep(2);continue
                async with websockets.connect(s.url,ping_interval=20,ping_timeout=20,max_size=2**22) as ws:
                    subs={}
                    for i,w in enumerate(wallets):
                        await ws.send(json.dumps({"jsonrpc":"2.0","id":i+1,"method":"logsSubscribe","params":[{"mentions":[w]},{"commitment":s.commitment}]}))
                    s.connected=True;back=1;log.info("WS connected, subscribing %d wallets",len(wallets))
                    while True:
                        if s.ver!=ver: log.info("WS watched set changed, resubscribing");break
                        try: raw=await asyncio.wait_for(ws.recv(),timeout=5)
                        except asyncio.TimeoutError: continue
                        now=time.time();s.last_msg=now;m=json.loads(raw)
                        if "id" in m and "result" in m: subs[m["result"]]=wallets[m["id"]-1];continue
                        if m.get("method")=="logsNotification":
                            p=m["params"];v=p["result"]["value"];w=subs.get(p["subscription"])
                            if w and not v.get("err"): s.notifications+=1;s.q.put((w,v["signature"],now,p["result"]["context"]["slot"]))
            except Exception as e:
                s.connected=False;s.reconnects+=1;log.warning("WS error (%s), reconnect in %ss",e,back)
                await asyncio.sleep(back);back=min(back*2,60)
            s.connected=False
