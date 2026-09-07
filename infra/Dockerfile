FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 libcairo2 ffmpeg fonts-dejavu-core curl unzip \
    && rm -rf /var/lib/apt/lists/*
# yt-dlp needs a JavaScript runtime or it warns that some formats may be
# missing -- a silent cap below the best available audio. This build only
# implements deno; --js-runtimes node is accepted and then ignored.
RUN curl -fsSL -o /tmp/deno.zip \
        https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
    && unzip -q /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip \
    && deno --version
RUN pip install --no-cache-dir \
        numpy scipy librosa soundfile verovio cairosvg pillow yt-dlp
# librosa's numba kernels and yt-dlp both want a writable home; the container
# runs as the invoking user so it cannot use root's.
ENV HOME=/tmp NUMBA_CACHE_DIR=/tmp/numba MPLCONFIGDIR=/tmp/mpl \
    PYTHONPATH=/infra PYTHONUNBUFFERED=1
WORKDIR /work
ENTRYPOINT ["python", "-m", "playalong"]
