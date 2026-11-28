FROM debian:sid-slim@sha256:07a3205e8583fdff12d2b05bd030f50437e9fd340ee616df0c05dc77af75767b AS python-base

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && useradd --uid 10001 --no-create-home app \
    && install -d -o app -g app /data

FROM python-base AS skill-worker
COPY requirements-skill.txt /requirements-skill.txt
RUN /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: -r /requirements-skill.txt
COPY skill_worker.py /skill_worker.py
COPY skill_network_client.py /skill_network_client.py
COPY superharry/skill_files.py /skill_files.py
COPY superharry/browser_tasks.py /browser_tasks.py
USER app
ENTRYPOINT ["/opt/venv/bin/python", "-EsB", "/skill_worker.py"]

FROM skill-worker AS skill-browser-worker
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs=24.22.3+dfsg+~cs24.19.2-1 npm=12.2.2+ds1-1 \
    && rm -rf /var/lib/apt/lists/*
COPY superharry/skills/playwright/ /opt/playwright-skill/
RUN cd /opt/playwright-skill/vendor \
    && npm ci --omit=dev \
    && npx playwright install --with-deps chromium \
    && chown -R app:app /opt/playwright-skill /opt/playwright-browsers \
    && chmod -R a+rX /opt/playwright-skill /opt/playwright-browsers
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    SKILL_PLAYWRIGHT_ENTRYPOINT=/opt/playwright-skill/main.mjs \
    SKILL_PLAYWRIGHT_SESSION_ENTRYPOINT=/opt/playwright-skill/session.mjs
USER app
LABEL org.superharry.runtime="playwright-browser" \
      org.superharry.node="24.22.3+dfsg+~cs24.19.2-1" \
      org.superharry.npm="12.2.2+ds1-1" \
      org.superharry.upstream-playwright-skill="dd47a6a023e249eb1b36e9e943eab89d0900865d"
ENTRYPOINT ["/opt/venv/bin/python", "-EsB", "/skill_worker.py"]

FROM python-base AS django
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=superharry.settings

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY superharry/ superharry/
COPY SOUL.md SOUL.md
COPY scripts/ scripts/
COPY tests/ tests/
RUN DJANGO_SECRET_KEY=build-only python -m django collectstatic --noinput

USER app
CMD ["sh", "-ec", "python -m django migrate --noinput && python -m django bootstrap_recovery && python -m django recover_chat_attempts && exec gunicorn --no-control-socket --workers 1 --threads 4 --bind 0.0.0.0:8000 superharry.wsgi:application"]
