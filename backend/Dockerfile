# One image, two roles: API (default CMD) and worker (`python -m app.cli worker`).
# The local models (Whisper, Piper voice, RapidOCR, embeddings, reranker) are downloaded at build
# time and the container then runs fully offline (HF_HUB_OFFLINE=1).
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MMA_MODEL_CACHE_DIR=/models MMA_FILE_DIR=/app/var/files
# No system packages are needed (PyAV bundles FFmpeg; OpenCV is the headless build).
# Optional build secret `extra_ca` supports networks with a TLS-inspecting proxy:
#   docker build --secret id=extra_ca,src=ca.crt ...  (not stored in the image)
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=extra_ca,required=false,mode=0444 \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements.txt
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY app ./app
# Bake the models into the image. The settings object needs a JWT secret to load; this throwaway
# value exists only for this build step and is not present in the final environment.
RUN --mount=type=secret,id=extra_ca,required=false,mode=0444 \
    if [ -f /run/secrets/extra_ca ]; then export REQUESTS_CA_BUNDLE=/run/secrets/extra_ca SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    MMA_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')" python -m app.cli warm-models \
    && chmod -R a+rX /models /usr/local/lib/python3.13/site-packages/rapidocr
ENV HF_HUB_OFFLINE=1
RUN useradd --uid 10001 --create-home mma && mkdir -p /app/var/files && chown -R mma /app/var
USER mma
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health/live', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "1"]
