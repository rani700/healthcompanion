# --- Stage 1: build the React frontend -------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python backend that also serves the built frontend -----------
FROM python:3.12-slim
WORKDIR /app

# Runtime deps from a pinned file (no drift between Docker and the codebase).
COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY config.py api.py ./
COPY src/ ./src/
COPY --from=frontend /fe/dist ./frontend_dist

# FastAPI serves these static files from the same origin as the API.
ENV HC_STATIC_DIR=/app/frontend_dist
ENV HC_DATA_DIR=/data
# Production: refuse to boot with the insecure default JWT secret.
ENV HC_ENV=production

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
