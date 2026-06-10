FROM python:3.12-slim

WORKDIR /opt/raceflag

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY raceflag/ raceflag/
COPY version.txt .

ENV DEMO_MODE=1 \
    RACEFLAG_CONFIG=/opt/raceflag/config.json \
    RACEFLAG_EFFECTS=/opt/raceflag/raceflag/effects/effects.json \
    RACEFLAG_VERSION=/opt/raceflag/version.txt \
    RACEFLAG_DIR=/opt/raceflag \
    RACEFLAG_REPO=""

EXPOSE 8080

CMD ["python", "-m", "raceflag.main"]
