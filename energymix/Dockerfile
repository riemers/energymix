ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.22

# --- Frontend (React) bouwen ---
FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Add-on ---
FROM ${BUILD_FROM}
WORKDIR /app
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt
COPY app/energymix ./energymix
COPY --from=frontend /src/app/energymix/static ./energymix/static

ENV PYTHONUNBUFFERED=1
CMD ["python3", "-m", "energymix"]
