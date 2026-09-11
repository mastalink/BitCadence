"""
ServiceNow connector (ITSM control tower).

Config keys (credentials belong in the encrypted secret store):
    SERVICENOW_INSTANCE_URL   e.g. https://acme.service-now.com
    SERVICENOW_USERNAME       basic-auth user (with SERVICENOW_PASSWORD), or
    SERVICENOW_TOKEN          OAuth bearer token (takes precedence)
    SERVICENOW_SYNC_QUERY     sysparm_query for pull_events
                              (default: active incidents in the AI agents queue)
    SERVICENOW_TARGET_ROLE    role that ingested incidents are addressed to
                              (default: claude)

Ingestion: open incidents matching SERVICENOW_SYNC_QUERY become MCO jobs.
Control: create/update/comment/resolve incidents via the Table API. The MCO
escalation bridge opens a ServiceNow incident when a job exhausts retries.
"""

from __future__ import annotations

from typing import List, Optional

import httpx

from mco.connectors.base import BaseConnector, ConnectorError

DEFAULT_SYNC_QUERY = "active=true^assignment_group.name=AI Agents"


class ServiceNowConnector(BaseConnector):
    name = "servicenow"

    def __init__(
        self,
        instance_url: str,
        username: str = "",
        password: str = "",
        token: str = "",
        sync_query: str = DEFAULT_SYNC_QUERY,
        target_role: str = "claude",
        timeout: float = 15.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        if not instance_url:
            raise ConnectorError("SERVICENOW_INSTANCE_URL is required")
        if not token and not (username and password):
            raise ConnectorError("ServiceNow needs SERVICENOW_TOKEN or SERVICENOW_USERNAME+SERVICENOW_PASSWORD")
        self.base_url = instance_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.sync_query = sync_query
        self.target_role = target_role
        self.timeout = timeout
        self._transport = transport  # test hook (httpx.MockTransport)

    @classmethod
    def from_config(cls, config) -> "ServiceNowConnector":
        return cls(
            instance_url=config.get("SERVICENOW_INSTANCE_URL") or "",
            username=config.get("SERVICENOW_USERNAME") or "",
            password=config.get("SERVICENOW_PASSWORD") or "",
            token=config.get("SERVICENOW_TOKEN") or "",
            sync_query=config.get("SERVICENOW_SYNC_QUERY") or DEFAULT_SYNC_QUERY,
            target_role=config.get("SERVICENOW_TARGET_ROLE") or "claude",
        )

    def _client(self) -> httpx.Client:
        kwargs: dict = {
            "base_url": self.base_url,
            "timeout": self.timeout,
            "headers": {"Accept": "application/json", "Content-Type": "application/json"},
        }
        if self.token:
            kwargs["headers"]["Authorization"] = f"Bearer {self.token}"
        else:
            kwargs["auth"] = (self.username, self.password)
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
            raise ConnectorError(f"ServiceNow HTTP {e.response.status_code}: {e.response.text[:200]}")
        except httpx.HTTPError as e:
            raise ConnectorError(f"ServiceNow request failed: {e}")

    # ── BaseConnector API ─────────────────────────────────────────────────────

    def health(self) -> dict:
        try:
            self._request("GET", "/api/now/table/incident", params={"sysparm_limit": 1})
            return {"ok": True, "detail": f"connected to {self.base_url}"}
        except ConnectorError as e:
            return {"ok": False, "detail": str(e)}

    def pull_events(self) -> List[dict]:
        data = self._request("GET", "/api/now/table/incident", params={
            "sysparm_query": self.sync_query,
            "sysparm_limit": 50,
            "sysparm_fields": "sys_id,number,short_description,description,urgency,state",
        })
        specs = []
        for inc in (data.get("result") or []):
            specs.append({
                "external_id": f"servicenow:{inc.get('sys_id')}",
                "title": f"[{inc.get('number', 'INC')}] {inc.get('short_description') or 'ServiceNow incident'}",
                "description": inc.get("description") or inc.get("short_description") or "",
                "target_agent_role": self.target_role,
                "input_payload": {
                    "external_id": f"servicenow:{inc.get('sys_id')}",
                    "connector": self.name,
                    "platform_ref": {"sys_id": inc.get("sys_id"), "number": inc.get("number"),
                                     "urgency": inc.get("urgency"), "state": inc.get("state")},
                    "prompt": (
                        f"ServiceNow incident {inc.get('number')}: {inc.get('short_description')}\n\n"
                        f"{inc.get('description') or ''}\n\n"
                        "Investigate and resolve. When done, send a job to role 'servicenow' with "
                        "action 'resolve_incident' to close it out."
                    ),
                },
            })
        return specs

    def actions(self) -> List[str]:
        return ["create_incident", "update_incident", "add_comment", "resolve_incident",
                "get_incident", "search_similar_incidents", "search_kb"]

    def execute_action(self, action: str, params: dict) -> dict:
        if action == "create_incident":
            body = {
                "short_description": params.get("short_description") or params.get("title") or "MCO incident",
                "description": params.get("description") or "",
                "urgency": str(params.get("urgency") or "2"),
                "caller_id": params.get("caller_id") or "",
            }
            res = self._request("POST", "/api/now/table/incident", json=body)
            rec = res.get("result") or {}
            return {"sys_id": rec.get("sys_id"), "number": rec.get("number")}
        if action == "update_incident":
            sys_id = params.get("sys_id") or self._require(params, "sys_id")
            res = self._request("PATCH", f"/api/now/table/incident/{sys_id}", json=params.get("fields") or {})
            return {"sys_id": sys_id, "updated": True, "result": bool(res)}
        if action == "add_comment":
            sys_id = self._require(params, "sys_id")
            self._request("PATCH", f"/api/now/table/incident/{sys_id}",
                          json={"comments": params.get("comment") or ""})
            return {"sys_id": sys_id, "commented": True}
        if action == "resolve_incident":
            sys_id = self._require(params, "sys_id")
            self._request("PATCH", f"/api/now/table/incident/{sys_id}", json={
                "state": "6",  # Resolved
                "close_code": params.get("close_code") or "Solved (Permanently)",
                "close_notes": params.get("close_notes") or "Resolved by BitCadence agent.",
            })
            return {"sys_id": sys_id, "resolved": True}
        if action == "get_incident":
            sys_id = self._require(params, "sys_id")
            res = self._request("GET", f"/api/now/table/incident/{sys_id}")
            return res.get("result") or {}
        if action == "search_similar_incidents":
            # The institutional memory buried in closed tickets: full-text match
            # (ServiceNow's 123TEXTQUERY321 keyword operator) over resolved/closed
            # incidents, returning the close notes - i.e. the prior fixes.
            query = self._require(params, "query")
            limit = int(params.get("limit") or 5)
            res = self._request("GET", "/api/now/table/incident", params={
                "sysparm_query": f"123TEXTQUERY321={query}^stateIN6,7",
                "sysparm_limit": limit,
                "sysparm_fields": "sys_id,number,short_description,close_code,close_notes,resolved_at",
            })
            return {"matches": [
                {"sys_id": r.get("sys_id"), "number": r.get("number"),
                 "short_description": r.get("short_description"),
                 "close_code": r.get("close_code"),
                 "close_notes": (r.get("close_notes") or "")[:600],
                 "resolved_at": r.get("resolved_at")}
                for r in (res.get("result") or [])
            ]}
        if action == "search_kb":
            # Published knowledge-base articles matching the problem text.
            query = self._require(params, "query")
            limit = int(params.get("limit") or 5)
            res = self._request("GET", "/api/now/table/kb_knowledge", params={
                "sysparm_query": f"123TEXTQUERY321={query}^workflow_state=published",
                "sysparm_limit": limit,
                "sysparm_fields": "sys_id,number,short_description,text",
            })
            return {"articles": [
                {"sys_id": r.get("sys_id"), "number": r.get("number"),
                 "short_description": r.get("short_description"),
                 "text": (r.get("text") or "")[:600]}
                for r in (res.get("result") or [])
            ]}
        raise ConnectorError(f"Unknown ServiceNow action: {action}")

    def escalate(self, job: dict, error: str) -> dict:
        """MCO escalation bridge: open an incident carrying the failure context."""
        return self.execute_action("create_incident", {
            "short_description": f"MCO escalation: {job.get('title', 'agent job failed')}",
            "description": (
                f"BitCadence job {job.get('id')} failed after exhausting retries.\n"
                f"Target role: {job.get('target_agent_role')}\n"
                f"Last error: {error}\n\n"
                f"Original instructions:\n{job.get('description') or ''}"
            ),
            "urgency": "1",
        })

    @staticmethod
    def _require(params: dict, key: str):
        val = params.get(key)
        if not val:
            raise ConnectorError(f"Missing required param: {key}")
        return val
