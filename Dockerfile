FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_TEAM_DATA_DIR=/data

WORKDIR /srv/agent-team
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir . \
    && groupadd -g 10001 agentteam \
    && useradd -u 10001 -g 10001 -d /data -s /usr/sbin/nologin agentteam \
    && mkdir -p /data \
    && chown 10001:10001 /data

USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/api/status',timeout=3).close()" || exit 1
CMD ["python", "-m", "agent_team.server", "--host", "0.0.0.0", "--port", "8765"]
