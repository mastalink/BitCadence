"""Fixed, no-LLM conductor tick adapter.

This is deliberately an adapter rather than a general execution endpoint.  It
does not accept a model, shell command, URL, release request, or IAM target
from EventBridge.  A later reviewed persistence/outbox implementation supplies
the five bounded operations behind :class:`Tick`.
"""
from __future__ import annotations

import json

STEPS = ("recover", "poll_evidence", "plan", "outbox", "dispatch")


class Tick:
    def __init__(self, operations):
        self.operations = operations

    def run(self):
        result = []
        for name in STEPS:
            operation = self.operations[name]
            result.append({"step": name, "result": operation()})
        return {"schema_version": 1, "mode": "fixed_no_llm_tick", "steps": result}


def _not_installed(name):
    return {"status": "not_installed", "reason": "durable_conductor_store_required", "operation": name}


def handler(event, context):
    # EventBridge payload is intentionally non-authoritative.  In particular it
    # cannot turn this scheduled adapter into an LLM, shell, URL, or release
    # dispatcher.
    del event, context
    tick = Tick({name: (lambda name=name: _not_installed(name)) for name in STEPS})
    return {"statusCode": 202, "body": json.dumps(tick.run(), separators=(",", ":"))}
