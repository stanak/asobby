FROM node:22-slim AS framedata

ARG TH123DATA_REF=21bfc9a8513625eb4e4a021cb9b2e474cd4fb986

RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /src/vendor/th123data
RUN git init \
  && git remote add origin https://github.com/stanak/th123data.git \
  && git fetch --depth 1 origin "${TH123DATA_REF}" \
  && git checkout FETCH_HEAD

RUN cd search && npm ci
ENV VITE_PARENT_URL=/
ENV VITE_GA_MEASUREMENT_ID=G-RJSLH3FRBR
RUN npm run build:search

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py db.py geoip.py post_redis.py client_release.py alembic.ini ./
COPY migrations ./migrations
COPY static ./static
COPY --from=framedata /src/vendor/th123data/docs ./static/data

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
