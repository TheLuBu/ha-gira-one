FROM ghcr.io/home-assistant/home-assistant:2026.11.2@sha256:493a07e8f6530fc640223f4a0a5248f20a61ca2f718bb1d31efbf08a2c5ea28f AS hardware
USER 1000:1000
WORKDIR /workspace
ENTRYPOINT []

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS development
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir homeassistant==2026.11.2 home-assistant-frontend==20261028.6 \
    pillow==13.0.0 mutagen==1.48.1 ifaddr==0.2.0 securetar==2026.10.0 cronsim==2.7 -r /tmp/requirements.txt
RUN pip install --no-cache-dir pyotp==2.10.0 PyQRCode==1.2.1
ENV HOME=/tmp
USER 1000:1000
WORKDIR /workspace
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
