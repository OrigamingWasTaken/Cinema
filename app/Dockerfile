# Stage 1: build the phone app (app/web) with Node.
FROM node:22-alpine AS web
WORKDIR /web
# Dependencies first, so editing the app's source reuses the npm ci layer.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build -- --outDir /web-out --emptyOutDir

# Stage 2: the API, which also serves the built app from cinema/static.
FROM python:3.12-slim
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cinema/ cinema/
COPY --from=web /web-out/ cinema/static/
EXPOSE 8888
CMD ["uvicorn", "--factory", "cinema.main:build", "--host", "0.0.0.0", "--port", "8888"]
