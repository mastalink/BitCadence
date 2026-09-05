FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . boto3
COPY infra/aws/lab/runtime.py /app/lab.py
COPY infra/aws/lab/probe.py /app/probe.py
RUN useradd --uid 1000 --create-home mco
USER mco
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/lab.py"]
