FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy
WORKDIR /app
RUN pip install --no-cache-dir playwright==1.49.1
COPY server.py arattai_pilot.py index.html ./
ENV CLOUD=1 DATA_DIR=/data AUTOSTART=1 PYTHONUNBUFFERED=1
CMD ["python", "server.py"]
