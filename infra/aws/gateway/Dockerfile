# The hub. Extends the repo's own image with the Postgres driver, the S3
# forwarder, and an entrypoint that mints the PostgREST JWT, runs migrations,
# seeds the cloud workers, and only then serves.
#   docker build -f infra/aws/gateway/Dockerfile -t <ecr>/gateway:latest .
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . "psycopg[binary]>=3.1" boto3

COPY infra/aws/gateway/entrypoint.py /app/entrypoint.py

RUN useradd --create-home --uid 1000 mco
USER mco
ENV HOME=/home/mco

EXPOSE 18789
CMD ["python", "/app/entrypoint.py"]
