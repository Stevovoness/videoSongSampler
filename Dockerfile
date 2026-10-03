# Video Sampler engine for a Linux server: renders projects and builds Shorts from the command line.
#   docker build -t vsampler .
# (includes the autopilot package: see the end of this file)
#   docker run --rm -v "$PWD/work:/work" vsampler auto --videos /work/speech.mp4 --song /work/tune.mid \
#       --preset shorts --out /work/short.mp4
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends rubberband-cli fonts-noto-color-emoji libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt \
    && pip install --no-cache-dir --no-deps basic-pitch==0.4.0

COPY vsampler/ vsampler/
COPY autopilot/ autopilot/
COPY assets/ assets/
COPY tests/ tests/
COPY main.py .

# caches and settings (paths.user_data_dir) go to a volume
ENV APPDATA=/data PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8765
ENTRYPOINT ["python", "-m", "vsampler"]
# The automation: docker run ... --entrypoint python vsampler -m autopilot run   (review page on port 8765;
# set review.host to 0.0.0.0 in autopilot/config.yaml and put it behind HTTPS, see docs/autopilot.md)
