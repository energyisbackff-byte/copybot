# Zetrax in Docker: PAPER books only (copy, consensus, Trend Rider, Narrative Rider, perps). The real-money guards
# (signer, perps guard) need their own system users and stay a systemd feature on the server (install.sh).
FROM python:3.12-slim
WORKDIR /opt/copybot
COPY . /opt/copybot
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -m copybot && mkdir -p /data && chown copybot /data
ENV COPYBOT_DB=/data/bot.db COPYBOT_HEARTBEAT=/data/heartbeat COPYBOT_MEMORY=/data/memory COPYBOT_PID=/data/bot.pid CB_STATE=/data/state
USER copybot
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import os,time,sys;sys.exit(0 if time.time()-os.path.getmtime('/data/heartbeat')<300 else 1)"
CMD ["python","main.py","run"]
