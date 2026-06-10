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

# Runtime deps only (skip test tooling).
COPY requirements.txt ./
RUN pip install --no-cache-dir \
      google-genai chromadb langchain-text-splitters python-dotenv \
      fastapi "uvicorn[standard]" python-multipart pyjwt

COPY config.py api.py ./
COPY src/ ./src/
COPY --from=frontend /fe/dist ./frontend_dist

# FastAPI serves these static files from the same origin as the API.
ENV HC_STATIC_DIR=/app/frontend_dist
ENV HC_DATA_DIR=/data

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
