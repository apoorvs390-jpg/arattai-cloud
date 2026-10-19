FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Signal ke Node packages ek baar (har user ke folder me symlink hota hai)
RUN mkdir -p /opt/pya && cd /opt/pya && npm init -y >/dev/null \
    && npm install --no-audit --no-fund --silent --save-exact bytebuffer@4.1.0 long@3.2.0 protobufjs@4.1.3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway.py server.py arattai_api.py index.html ./
ENV DATA_DIR=/data NODE_SHARED=/opt/pya/node_modules PYTHONUNBUFFERED=1
CMD ["python", "gateway.py"]
