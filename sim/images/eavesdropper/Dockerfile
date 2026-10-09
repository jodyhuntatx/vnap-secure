# Passive V2X listener (untrusted party): sees only raw frames on the message network.
FROM python:3.12-slim
RUN pip install --no-cache-dir asn1tools==0.169.0
COPY eavesdropper.py /eavesdropper/eavesdropper.py
COPY asn1 /eavesdropper/asn1
ENV PYTHONUNBUFFERED=1
VOLUME /logs
ENTRYPOINT ["python3", "/eavesdropper/eavesdropper.py"]
