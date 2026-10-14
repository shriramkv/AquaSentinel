# Build from the WORKSPACE root so the fish-health model is in the build context:
#   docker build -f Aquaculture_Webapp/Dockerfile -t aquasentinel .
#   docker run -p 8080:8080 --env-file Aquaculture_Webapp/.env aquasentinel
FROM node:22-slim AS web
WORKDIR /web
COPY Aquaculture_Webapp/frontend/package*.json ./
RUN npm ci
COPY Aquaculture_Webapp/frontend ./
RUN npm run build

FROM python:3.12-slim
# LightGBM needs the OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
# Same relative layout as the workspace, so MODEL_DIR=../Aquaculture_Model and the requirements include resolve
COPY Aquaculture_Model/requirements.txt Aquaculture_Model/requirements.txt
COPY Aquaculture_Webapp/backend/requirements.txt Aquaculture_Webapp/backend/requirements.txt
RUN pip install --no-cache-dir -r Aquaculture_Webapp/backend/requirements.txt
COPY Aquaculture_Model/app Aquaculture_Model/app
COPY Aquaculture_Model/models Aquaculture_Model/models
COPY Aquaculture_Webapp/backend/aquasentinel Aquaculture_Webapp/backend/aquasentinel
COPY Aquaculture_Webapp/backend/data/lake.geojson Aquaculture_Webapp/backend/data/cpcb_criteria.json Aquaculture_Webapp/backend/data/
COPY --from=web /web/dist Aquaculture_Webapp/frontend/dist

WORKDIR /srv/Aquaculture_Webapp/backend
ENV PORT=8080
EXPOSE 8080
# One worker: live state (boat, detector, WebSocket clients) is held in process memory
CMD ["sh", "-c", "uvicorn aquasentinel.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
