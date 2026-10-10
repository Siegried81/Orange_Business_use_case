# Container image for the Innovation Radar dashboard (Streamlit) and pipeline.
#
# Why it is built this way:
# - python:3.12-slim: a small Debian base with wheels for every pinned
#   dependency (pandas, plotly, streamlit), so no compiler is installed.
# - requirements.txt is installed BEFORE the source is copied, so a code change
#   rebuilds only the last, cheap layer.
# - The app runs as an unprivileged user, so a dependency or a prompt gone
#   wrong cannot write outside /app.
# - radar.db travels in the image (it is committed), so the dashboard works
#   with no volume; docker-compose.yml mounts the host copy over it to show
#   the latest pipeline run instead. Secrets come from .env at run time only:
#   .dockerignore keeps it out of the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 10001 app
COPY --chown=app:app . .
USER app

EXPOSE 8501

# Streamlit's liveness endpoint, polled with python because slim ships no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"]

# $PORT when a platform sets one (Render), 8501 otherwise. `exec` keeps
# Streamlit as PID 1 so `docker stop` reaches it directly.
CMD exec python -m streamlit run app/streamlit_app.py --server.port="${PORT:-8501}" --server.address=0.0.0.0
