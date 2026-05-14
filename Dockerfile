FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.6

COPY pyproject.toml .
RUN uv sync --no-dev

COPY . .

EXPOSE 8010 8011
