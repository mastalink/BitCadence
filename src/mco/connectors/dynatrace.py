"""
Dynatrace connector (observability / AIOps).

Config keys (token belongs in the encrypted secret store):
    DYNATRACE_BASE_URL     e.g. https://abc12345.live.dynatrace.com
    DYNATRACE_API_TOKEN    API token with problems.read / problems.write scopes
    DYNATRACE_TARGET_ROLE  role that ingested problems are addressed to
                           (default: claude)

Ingestion: OPEN problems (Problems API v2) become MCO jobs so an AI agent can
triage them. Control: comment on and close problems from agent workflows.
"""

from __future__ import annotations

from typing import List, Optional

import httpx

from mco.connectors.base import BaseConnector, ConnectorError


class DynatraceConnector(BaseConnector):
    name = "dynatrace"

    def __init__(
        self,
        base_url: str,
        api_token: str,
        target_role: str = "claude",
        timeout: float = 15.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        if not base_url or not api_token:
            raise ConnectorError("Dynatrace needs DYNATRACE_BASE_URL and DYNATRACE_API_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.target_role = target_role
        self.timeout = timeout
        self._transport = transport  # test hook (httpx.MockTransport)

    @classmethod
    def from_config(cls, config) -> "DynatraceConnector":
        return cls(
            base_url=config.get("DYNATRACE_BASE_URL") or "",
            api_token=config.get("DYNATRACE_API_TOKEN") or "",
            target_role=config.get("DYNATRACE_TARGET_ROLE") or "claude",
        )

    def _client(self) -> httpx.Client:
        kwargs: dict = {
            "base_url": self.base_url,
            "timeout": self.timeout,
            "headers": {
                "Authorization": f"Api-Token {self.api_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            with self._client() as c:
                res = c.request(method, path, **kwargs)
                res.raise_for_status()
                return res.json() if res.content else {}
        except httpx.HTTPStatusError as e:
            raise ConnectorError(f"Dynatrace HTTP {e.response.status_code}: {e.response.text[:200]}")
        except httpx.HTTPError as e:
            raise ConnectorError(f"Dynatrace request failed: {e}")

    # ── BaseConnector API ─────────────────────────────────────────────────────

    def health(self) -> dict:
        try:
            self._request("GET", "/api/v2/problems", params={"pageSize": 1})
            return {"ok": True, "detail": f"connected to {self.base_url}"}
        except ConnectorError as e:
            return {"ok": False, "detail": str(e)}

    def pull_events(self) -> List[dict]:
        data = self._request("GET", "/api/v2/problems", params={
            "problemSelector": 'status("OPEN")',
            "pageSize": 50,
        })
        specs = []
        for prob in (data.get("problems") or []):
            pid = prob.get("problemId")
            impacted = ", ".join(
                e.get("name", "?") for e in (prob.get("impactedEntities") or [])[:5]
            )
            specs.append({
                "external_id": f"dynatrace:{pid}",
                "title": f"[{prob.get('displayId', pid)}] {prob.get('title') or 'Dynatrace problem'}",
                "description": (
                    f"Severity: {prob.get('severityLevel')} | Impact: {prob.get('impactLevel')}\n"
                    f"Impacted: {impacted or 'n/a'}"
                ),
                "target_agent_role": self.target_role,
                "input_payload": {
                    "external_id": f"dynatrace:{pid}",
                    "connector": self.name,
                    "platform_ref": {"problemId": pid, "displayId": prob.get("displayId"),
                                     "severityLevel": prob.get("severityLevel")},
                    "prompt": (
                        f"Dynatrace problem {prob.get('displayId', pid)}: {prob.get('title')}\n"
                        f"Severity {prob.get('severityLevel')}, impacted: {impacted or 'n/a'}.\n\n"
                        "Triage this problem. Record findings by sending a job to role "
                        "'dynatrace' with action 'add_comment', and 'close_problem' once resolved."
                    ),
                },
            })
        return specs

    def actions(self) -> List[str]:
        return ["add_comment", "close_problem", "get_problem", "list_problems"]

    def execute_action(self, action: str, params: dict) -> dict:
        if action == "add_comment":
            pid = self._require(params, "problem_id")
            self._request("POST", f"/api/v2/problems/{pid}/comments", json={
                "message": params.get("comment") or params.get("message") or "",
                "context": "BitCadence",
            })
            return {"problem_id": pid, "commented": True}
        if action == "close_problem":
            pid = self._require(params, "problem_id")
            self._request("POST", f"/api/v2/problems/{pid}/close", json={
                "message": params.get("message") or "Closed by BitCadence agent.",
            })
            return {"problem_id": pid, "closed": True}
        if action == "get_problem":
            pid = self._require(params, "problem_id")
            return self._request("GET", f"/api/v2/problems/{pid}")
        if action == "list_problems":
            data = self._request("GET", "/api/v2/problems", params={
                "problemSelector": params.get("selector") or 'status("OPEN")',
                "pageSize": int(params.get("limit") or 20),
            })
            return {"problems": data.get("problems") or []}
        raise ConnectorError(f"Unknown Dynatrace action: {action}")

    @staticmethod
    def _require(params: dict, key: str):
        val = params.get(key)
        if not val:
            raise ConnectorError(f"Missing required param: {key}")
        return val
