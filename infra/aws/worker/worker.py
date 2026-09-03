"""A cloud worker: BitCadence's fifteen-line SDK, backed by Amazon Bedrock.

No vendor CLI, no API key in a file. The task's IAM role is the only
credential AWS ever sees; the worker's authority over WORK comes from its
BitCadence token, which is the point - the governance runtime, not the cloud
provider, decides what this process may do.

One deliberate property for the chaos suite: `handle()` is long enough to be
interrupted. It streams the model response in chunks and, between chunks,
checks whether the job it holds is still its to finish. That check is where
the kill-switch pavilion either passes or fails - today the gateway does not
tell an in-flight worker to stop, so the check will find nothing and this
worker will run to completion. That is recorded, not hidden.
"""

from __future__ import annotations

import json
import os
import sys
import time

import boto3
import httpx
from loguru import logger

from mco.sdk import BitCadenceAgent

GATEWAY = os.environ["GATEWAY_URL"].rstrip("/")
ROLE = os.environ["ROLE"]
INSTANCE = os.environ.get("INSTANCE_ID") or f"{ROLE}-cloud-1"
TOKEN = os.environ["MCO_AGENT_TOKEN"]
MODEL = os.environ["BEDROCK_MODEL_ID"]
POLL = float(os.environ.get("POLL_INTERVAL", "5"))

logger.remove()
logger.add(sys.stdout, serialize=True, level="INFO")

bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION"))

agent = BitCadenceAgent(role=ROLE, instance_id=INSTANCE, token=TOKEN, gateway=GATEWAY)
agent.poll_interval = POLL


class StopRequested(Exception):
    """Raised mid-job when the gateway says this attempt is no longer ours."""


def _still_mine(job_id: str) -> bool:
    """Ask the gateway whether this job is still leased to us.

    Returns True on any error: a transient network blip must not abort real
    work. A partitioned worker therefore keeps going - which is exactly the
    stale-writer scenario WS1 fences at completion time, not here.
    """
    try:
        # There is no single-job GET; the board is listed and filtered.
        r = httpx.get(
            f"{GATEWAY}/api/jobs",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=5,
        )
        if r.status_code != 200:
            return True
        job = next((j for j in r.json() if j.get("id") == job_id), None)
        if job is None:
            return True
        status = job.get("status")
        owner = job.get("leased_by_instance_id") or job.get("leased_by")
        if status in ("cancelled", "rejected", "failed", "completed"):
            return False
        if owner and owner != INSTANCE:
            return False
        return True
    except Exception:
        return True


@agent.handler
def handle(job: dict, prompt: str):
    job_id = job["id"]
    logger.info({"event": "job.start", "job_id": job_id, "role": ROLE, "model": MODEL})

    # Stream so there are interruption points. A single blocking call would
    # make the kill-switch pavilion untestable by construction.
    text_parts: list[str] = []
    resp = bedrock.converse_stream(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        system=[{"text": (
            f"You are the '{ROLE}' worker in a governed BitCadence fleet running in AWS. "
            "Be concrete and brief. If the task asks you to take an action in an external "
            "system, describe exactly what you would do and stop - the governance runtime "
            "decides whether it happens."
        )}],
        inferenceConfig={"maxTokens": 1200, "temperature": 0.2},
    )

    chunks_since_check = 0
    for event in resp["stream"]:
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"]["delta"]
            if "text" in delta:
                text_parts.append(delta["text"])
                chunks_since_check += 1
                if chunks_since_check >= 20:
                    chunks_since_check = 0
                    if not _still_mine(job_id):
                        logger.warning({"event": "job.stop_requested", "job_id": job_id})
                        raise StopRequested(f"job {job_id} is no longer leased to {INSTANCE}")
        elif "messageStop" in event:
            break

    result = "".join(text_parts).strip()
    logger.info({"event": "job.done", "job_id": job_id, "chars": len(result)})

    return result, {
        "summary": result[:240],
        "worker": INSTANCE,
        "model": MODEL,
        "decisions": [],
        "gotchas": [],
    }


if __name__ == "__main__":
    logger.info({"event": "worker.boot", "role": ROLE, "instance": INSTANCE, "gateway": GATEWAY})
    # The gateway may still be seeding agents at first boot; the SDK's run
    # loop already retries a failed poll cycle, so nothing to add here.
    agent.run()
