# Per-run PKI service: C-ITS-PKI (CA hierarchy, butterfly key expansion) plus the refill
# protocol on the run's control broker. Build with build.sh, which adds C-ITS-PKI's sources.
FROM python:3.12-slim
RUN pip install --no-cache-dir "cryptography>=42.0.0" "tinyec>=0.4.0" "asn1tools>=0.167.0" paho-mqtt==2.1.0
COPY cits-pki /opt/cits-pki
COPY pki_service.py /usr/local/bin/pki_service.py
ENV PYTHONUNBUFFERED=1 CITS_PKI_HOME=/opt/cits-pki
VOLUME /pki
ENTRYPOINT ["python3", "/usr/local/bin/pki_service.py"]
CMD ["run"]
