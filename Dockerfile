FROM ghcr.io/home-assistant/home-assistant:2026.11.2@sha256:493a07e8f6530fc640223f4a0a5248f20a61ca2f718bb1d31efbf08a2c5ea28f AS dependencies
RUN uv venv --system-site-packages /opt/dev
COPY requirements.txt /tmp/requirements.txt
RUN uv pip install --python /opt/dev/bin/python -r /tmp/requirements.txt

FROM ghcr.io/home-assistant/home-assistant:2026.11.2@sha256:493a07e8f6530fc640223f4a0a5248f20a61ca2f718bb1d31efbf08a2c5ea28f AS development
COPY --from=dependencies /opt/dev /opt/dev
ENV PATH="/opt/dev/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
WORKDIR /workspace
ENTRYPOINT []
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
