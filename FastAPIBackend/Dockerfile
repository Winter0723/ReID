FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace \
    APP_ENV=production \
    GALLERY_DIR=/workspace/FastAPIBackend/gallery \
    OBJECT_STORAGE_DIR=/workspace/FastAPIBackend/object_storage

WORKDIR /workspace/FastAPIBackend

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 ffmpeg fonts-noto-cjk \
    && apt-get clean

COPY FastAPIBackend/requirements.txt /workspace/FastAPIBackend/requirements.txt
COPY FastAPIBackend/requirements-editing.txt /workspace/FastAPIBackend/requirements-editing.txt
ARG INSTALL_SCENIC_TRACKING=0
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --require-hashes --retries 5 --timeout 120 -r /workspace/FastAPIBackend/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    if [ "$INSTALL_SCENIC_TRACKING" = "1" ]; then pip install --require-hashes -r requirements-editing.txt; fi

COPY FastAPIBackend /workspace/FastAPIBackend
COPY model /workspace/model
RUN groupadd --gid 10001 myreid && useradd --uid 10001 --gid myreid --create-home myreid \
    && mkdir -p /workspace/FastAPIBackend/gallery /workspace/FastAPIBackend/object_storage \
    && chown -R myreid:myreid /workspace \
    && chmod +x /workspace/FastAPIBackend/docker/entrypoint.sh
USER 10001:10001

EXPOSE 2889
VOLUME ["/workspace/FastAPIBackend/gallery", "/workspace/FastAPIBackend/object_storage", "/models"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:2889/api/v1/healthz', timeout=3).read()"
ENTRYPOINT ["/workspace/FastAPIBackend/docker/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "2889"]
