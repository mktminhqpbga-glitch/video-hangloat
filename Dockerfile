# Muse AI Studio (ban Mac) — chay tren Render (Docker).
# Render tu dat bien PORT (va RENDER=true); app bind 0.0.0.0 theo bien do.
# Build context: goc repo. Khong can bien moi truong nao khac.
FROM python:3.11-slim

# ffmpeg de ghep video (tinh nang Ghep video trong app).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY muse-ai-studio-mac/app.py ./app.py

# Danh dau chay tren Render/container: app bind 0.0.0.0, khong tu mo trinh duyet.
ENV RENDER=true
ENV PORT=8765
EXPOSE 8765

CMD ["python3", "app.py"]
