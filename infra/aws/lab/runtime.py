"""Private TLS hub and short-lived spokes. No credentials in userdata or images."""
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time

import boto3
from botocore.config import Config

REGION = "us-east-1"
PREFIX = "bitcadence-lab"
ROOT = Path("/mco")


def secret(client, name, generate=False):
    try:
        return client.get_secret_value(SecretId=f"{PREFIX}/{name}")["SecretString"]
    except client.exceptions.ResourceNotFoundException:
        if not generate:
            raise
        value = secrets.token_urlsafe(48)
        client.put_secret_value(SecretId=f"{PREFIX}/{name}", SecretString=value)
        return value


def hub(client):
    ROOT.mkdir(exist_ok=True)
    os.umask(0o077)
    token = secret(client, "operator", generate=True)
    os.environ.update(MCO_LOCAL_TOKEN=token, MCO_STORE_BACKEND="local",
        MCO_LOCAL_STORE_PATH=str(ROOT / "local.db"), MCO_ENV_FILE=str(ROOT / "lab.env"),
        MCO_EVIDENCE_ACK_REQUIRED="true", MCO_EVIDENCE_RETENTION_DAYS="1", NTFY_TOPIC="")
    key, cert = ROOT / "tls.key", ROOT / "tls.crt"
    if not key.exists() or not cert.exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "90", "-subj", "/CN=bitcadence-lab",
            "-addext", "subjectAltName=IP:10.43.1.10,DNS:localhost,IP:127.0.0.1"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client.put_secret_value(SecretId=f"{PREFIX}/tls-ca", SecretString=cert.read_text())
    from mco.localstore import LocalStore, seed_local_operator
    from mco.orchestrator import routes
    store = LocalStore(ROOT / "local.db")
    seed_local_operator(store, token)
    for role in ("worker", "reviewer"):
        worker_token = secret(client, role, generate=True)
        store.table("agent_registry").upsert({"instance_id": f"{role}-lab", "role": role,
            "status": "offline", "auth_token_hash": hashlib.sha256(worker_token.encode()).hexdigest(),
            "org_id": "default", "scopes": ["jobs:read", "jobs:write", "context:read", "context:write", "agents:read"]}).execute()
    routes._db_client = store
    from mco.cli import create_app
    import uvicorn
    app = create_app()
    # One maintenance loop (main HTTP app); TLS listener shares the same app/store.
    tls = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=18790, lifespan="off",
        ssl_certfile=str(cert), ssl_keyfile=str(key)))
    threading.Thread(target=tls.run, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=18789)


def spoke(client, role):
    deadline = time.monotonic() + min(600, int(os.getenv("LAB_MAX_SECONDS", "600")))
    while True:
        try:
            token = secret(client, role)
            certificate = secret(client, "tls-ca")
            if "BEGIN CERTIFICATE" not in certificate:
                raise ValueError("Hub certificate is not ready")
            break
        except (client.exceptions.ResourceNotFoundException, ValueError):
            if time.monotonic() >= deadline:
                raise RuntimeError("Hub did not initialize credentials in time")
            time.sleep(5)
    ca = Path("/tmp/hub-ca.pem")
    ca.write_text(certificate)
    os.environ["SSL_CERT_FILE"] = str(ca)
    from mco.sdk import BitCadenceAgent
    agent = BitCadenceAgent(role=role, instance_id=f"{role}-lab", token=token, gateway="https://10.43.1.10:18790")
    bedrock = boto3.client("bedrock-runtime", region_name=REGION,
        config=Config(connect_timeout=5, read_timeout=45, retries={"max_attempts": 1}))
    completed = 0

    @agent.handler
    def handle(job, prompt):
        agent.checkpoint()
        if (job.get("input_payload") or {}).get("lab_mode") == "bedrock":
            response = bedrock.converse(modelId="amazon.nova-micro-v1:0",
                messages=[{"role": "user", "content": [{"text": prompt[:4000]}]}],
                inferenceConfig={"maxTokens": 128, "temperature": 0})
            agent.checkpoint()
            return "".join(part.get("text", "") for part in response["output"]["message"]["content"])
        # Deterministic real computation exercises transport, leases and evidence at no model cost.
        return "sha256:" + hashlib.sha256(prompt.encode()).hexdigest()

    while time.monotonic() < deadline and completed < 10:
        try:
            for job in agent.client.inbox() or []:
                if completed >= 10 or time.monotonic() >= deadline:
                    break
                if agent.process_job(job):
                    completed += 1
        except Exception as exc:
            print(f"Spoke retry: {type(exc).__name__}", flush=True)
        time.sleep(3)
    print(f"Spoke {role} finished bounded session: {completed} jobs", flush=True)


if __name__ == "__main__":
    client = boto3.client("secretsmanager", region_name=REGION)
    role = os.environ["LAB_ROLE"]
    if role == "hub":
        hub(client)
    elif role in {"worker", "reviewer"}:
        spoke(client, role)
    else:
        raise SystemExit("Unknown lab role")
