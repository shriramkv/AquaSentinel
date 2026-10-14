FROM python:3.12-slim

# LightGBM needs the OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
# Dependencies first, so this layer is cached until requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the serving code and the trained model go into the image; training/ and tests/ stay out
COPY app ./app
COPY models ./models

ENV POND_TIMEZONE=Asia/Kolkata \
    ALLOWED_ORIGINS=* \
    CRITICAL_WEIGHT=1.5 \
    LOG_LEVEL=INFO
EXPOSE 8000
# Logs go to stderr: read them with `docker logs <container>`
# Single worker: the /ingest live buffers live in process memory
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
