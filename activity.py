"""Bot activity stream for the dashboard's live scene (/api/activity). Best effort: never raises, never blocks trading.
Rows are small JSON blobs; the table is pruned to the newest ~3000 rows. No secrets are ever written here."""
import json,time,logging
log=logging.getLogger("bot")
DDL="create table if not exists activity(id integer primary key,ts real,kind text,data text)"
_n=0
def act(db,kind,**d):
    global _n
    try:
        cur=db.c.execute("insert into activity(ts,kind,data) values(?,?,?)",(time.time(),kind,json.dumps(d,default=str,separators=(",",":"))))
        _n+=1
        if _n%200==0: db.c.execute("delete from activity where id<?",(cur.lastrowid-3000,))
        db.c.commit()
    except Exception as e: log.debug("activity %s: %s",kind,e)
