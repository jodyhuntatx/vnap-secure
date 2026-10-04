# Mobility client: drives stations along routes by publishing their positions on the control
# channel (vnap/position/<station id>), which socktap follows with --position-control-broker.
FROM python:3.12-slim
RUN pip install --no-cache-dir paho-mqtt==2.1.0
COPY mobility.py /mobility/mobility.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/mobility/mobility.py"]
