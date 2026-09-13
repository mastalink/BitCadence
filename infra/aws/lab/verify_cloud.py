"""Wait for SSM, then run acceptance on the hub over its authenticated control channel."""
import json
import subprocess
import sys
import time


def aws(*args):
    result = subprocess.run(["aws", *args, "--region", "us-east-1", "--output", "json"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout or "{}")


def main():
    outputs = json.load(open(sys.argv[1], encoding="utf-8"))
    instances = outputs["instances"]["value"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        online = {i["InstanceId"] for i in aws("ssm", "describe-instance-information")["InstanceInformationList"] if i["PingStatus"] == "Online"}
        if set(instances.values()) <= online:
            break
        time.sleep(10)
    else:
        raise RuntimeError("Not all lab machines joined SSM within 10 minutes")
    commands = ["for i in $(seq 1 90); do curl -fsS http://127.0.0.1:18789/readyz >/dev/null && break; sleep 5; done",
                "docker exec bitcadence python /app/probe.py"]
    sent = aws("ssm", "send-command", "--instance-ids", instances["hub"], "--document-name", "AWS-RunShellScript",
        "--parameters", json.dumps({"commands": commands, "executionTimeout": ["600"]}), "--timeout-seconds", "600")
    command_id = sent["Command"]["CommandId"]
    deadline = time.monotonic() + 650
    while time.monotonic() < deadline:
        time.sleep(5)
        result = aws("ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", instances["hub"])
        if result["Status"] in {"Pending", "InProgress", "Delayed"}:
            continue
        print(result.get("StandardOutputContent", ""))
        if result["Status"] != "Success":
            print(result.get("StandardErrorContent", ""), file=sys.stderr)
            raise RuntimeError("Cloud acceptance failed: " + result["Status"])
        return
    raise RuntimeError("Cloud acceptance timed out")


if __name__ == "__main__":
    main()
