"""Real TLS/API/SDK smoke inside the image; AWS calls are explicitly stubbed."""
import importlib.util
import os
from pathlib import Path
import threading
import time
import httpx


class Secrets:
    def get_secret_value(self, SecretId):
        return {"SecretString": "local-smoke-" + SecretId.rsplit("/", 1)[-1]}

    def put_secret_value(self, **kwargs):
        return {}


class Sink:
    def put_object(self, **kwargs):
        assert kwargs["ObjectLockMode"] == "COMPLIANCE"
        return {"VersionId": "local-stub-version"}


def main():
    spec = importlib.util.spec_from_file_location("lab", "/app/lab.py")
    lab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lab)
    os.environ["MCO_EVIDENCE_BUCKET"] = "local-smoke-only"
    thread = threading.Thread(target=lab.hub, args=(Secrets(),), daemon=True)
    thread.start()
    for _ in range(100):
        try:
            if httpx.get("http://127.0.0.1:18789/readyz").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(.1)
    else:
        raise AssertionError("Hub never became ready")
    from mco.orchestrator import evidence
    evidence._sink = lambda: Sink()
    os.environ["SSL_CERT_FILE"] = "/mco/tls.crt"
    client = httpx.Client(base_url="https://127.0.0.1:18790", headers={"Authorization": "Bearer local-smoke-operator"})
    response = client.post("/api/jobs", json={"title": "TLS smoke", "description": "checksum", "target_agent_role": "worker"})
    response.raise_for_status()
    from mco.sdk import BitCadenceAgent
    agent = BitCadenceAgent(role="worker", instance_id="worker-lab", token="local-smoke-worker", gateway="https://127.0.0.1:18790")
    agent.handler(lambda job, prompt: "verified-tls-sdk")
    assert agent.run_once() == 1
    job = response.json().get("job", response.json())
    result = client.get("/api/jobs")
    result.raise_for_status()
    record = next(row for row in result.json() if row["id"] == job["id"])
    assert record["status"] == "completed", record
    print("PASS real hub TLS, certificate verification, scoped worker auth, SDK lease and completion; AWS sink stubbed")


if __name__ == "__main__":
    main()
