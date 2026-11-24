# Verification collector and warning probe: records what every station receives (security
# reports) from its embedded broker, and triggers DENMs on chosen stations.
FROM python:3.12-slim
RUN pip install --no-cache-dir paho-mqtt==2.1.0
COPY collector.py /collector/collector.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/collector/collector.py"]
