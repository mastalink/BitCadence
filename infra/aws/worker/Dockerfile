# Build from the repo root so the SDK is the one in this checkout:
#   docker build -f infra/aws/worker/Dockerfile -t <ecr>/worker:latest .
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . boto3

COPY infra/aws/worker/worker.py /app/worker.py

RUN useradd --create-home mco
USER mco
ENV HOME=/home/mco

CMD ["python", "/app/worker.py"]
