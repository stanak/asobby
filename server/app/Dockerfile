FROM node:22-slim AS framedata

WORKDIR /src/vendor/th123data
COPY vendor/th123data/ .
RUN cd search && npm ci
RUN npm run build:search

FROM python:3.12-slim

WORKDIR /app

COPY server/app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app/main.py server/app/db.py server/app/geoip.py server/app/post_redis.py server/app/client_release.py server/app/alembic.ini ./
COPY server/app/migrations ./migrations
COPY server/app/static ./static
COPY --from=framedata /src/vendor/th123data/docs ./static/data

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
