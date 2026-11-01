# Supervisor >= 2026.04.0 не передаёт BUILD_FROM, поэтому базовый образ задан явно.
FROM ghcr.io/home-assistant/base:latest

# ffmpeg режет видео события на кадры.
# py3-pillow склеивает кадры в одну картинку для Llama 3.2 Vision (vlm/grid.py).
# tzdata обязателен: без него Alpine игнорирует TZ и всё живёт в UTC —
# время событий уезжало бы на три часа относительно израильского.
RUN apk add --no-cache python3 py3-pip py3-pillow ffmpeg tzdata \
    && pip3 install --no-cache-dir --break-system-packages "paho-mqtt>=2.0"

COPY run.py        /app/run.py
COPY main.py       /app/main.py
COPY config.py     /app/config.py
COPY logs.py       /app/logs.py
COPY prompts/      /app/prompts/
COPY frigate/      /app/frigate/
COPY vlm/          /app/vlm/
COPY storage/      /app/storage/

WORKDIR /app
CMD ["python3", "/app/run.py"]
