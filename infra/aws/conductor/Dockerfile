# The clock. Runs once per schedule tick, narrates the pavilions, files the
# evidence, rewrites the status page, exits.
#   docker build -f infra/aws/conductor/Dockerfile -t <ecr>/conductor:latest .
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . "psycopg[binary]>=3.1" boto3

COPY infra/aws/conductor/conductor.py /app/conductor.py

RUN useradd --create-home mco
USER mco
ENV HOME=/home/mco

CMD ["python", "/app/conductor.py"]
