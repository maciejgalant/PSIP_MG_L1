
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        gcc \
        g++ \
        gdal-bin \
        python3-gdal \
        libgdal-dev \
        proj-bin \
        libproj-dev \
    && rm -rf /var/lib/apt/lists/*

# Utworzenie srodowiska Python
RUN python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

# Instalacja bibliotek Python
RUN pip install --no-cache-dir \
    h3 \
    geopandas \
    shapely \
    pyogrio \
    fiona \
    psycopg2-binary \
    sqlalchemy \
    geoalchemy2

WORKDIR /app

COPY ./app/ /app/
COPY ./data/ /app/data/