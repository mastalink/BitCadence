"""Optional TypeSafe/Jev decision provider.

Jev supplies typed semantic judgments; it is never an authority source.  The
provider is disabled by default, keeps credentials in SecretVault, validates
the wire response, and returns an evidence-bearing DecisionReceipt for both
success and deterministic fallback outcomes.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

import httpx

from mco.secret_vault import (
    SecretNotFoundError,
    SecretRef,
    VaultError,
    build_secret_vault,
)


BASE_URL = "https://api.typesafe.ai"
SYSTEM_ONE_PATH = "/v1/systemone"
MODELS_PATH = "/v1/models"
MODES = frozenset({"disabled", "shadow", "assist", "active"})


class JevConfigurationError(ValueError):
    pass


class JevProtocolError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def secret_ref(org_id: str = "default") -> SecretRef:
    return SecretRef(org_id=org_id, scope="typesafe-jev", name="api_key")


@dataclass(frozen=True)
class JevConfig:
    mode: str = "disabled"
    model: str = "jev-latest"
    timeout_seconds: float = 5.0
    max_retries: int = 1

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise JevConfigurationError("mode must be disabled, shadow, assist, or active")
        if not self.model or not isinstance(self.model, str):
            raise JevConfigurationError("model is required")
        if self.mode in {"assist", "active"} and self.model.endswith("-latest"):
            raise JevConfigurationError("assist and active modes require an exact pinned model")
        if not (0.1 <= float(self.timeout_seconds) <= 60.0):
            raise JevConfigurationError("timeout_seconds must be between 0.1 and 60")
        if int(self.max_retries) not in (0, 1, 2):
            raise JevConfigurationError("max_retries must be 0, 1, or 2")


@dataclass(frozen=True)
class DecisionReceipt:
    use_case_id: str
    question_set_version: str
    question_set_digest: str
    model: Optional[str]
    state_digest: str
    answers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    probabilities: Dict[str, Dict[str, float]] = field(default_factory=dict)
    confidence: Dict[str, Optional[float]] = field(default_factory=dict)
    latency_ms: Optional[int] = None
    usage: Dict[str, Optional[int]] = field(default_factory=dict)
    request_id: Optional[str] = None
    mode: str = "disabled"
    outcome: str = "disabled"
    error_class: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fallback_receipt(
    config: JevConfig,
    use_case_id: str,
    question_set_version: str,
    questions: Mapping[str, Any],
    state: Any,
    outcome: str,
    error_class: Optional[str] = None,
    latency_ms: Optional[int] = None,
) -> DecisionReceipt:
    return DecisionReceipt(
        use_case_id=use_case_id,
        question_set_version=question_set_version,
        question_set_digest=_digest(questions),
        model=config.model if config.mode != "disabled" else None,
        state_digest=_digest(state),
        latency_ms=latency_ms,
        mode=config.mode,
        outcome=outcome,
        error_class=error_class,
    )


def _validate_questions(questions: Mapping[str, Any]) -> None:
    if not isinstance(questions, Mapping) or not questions:
        raise JevProtocolError("at least one question is required")
    for name, question in questions.items():
        if not isinstance(name, str) or not name or not isinstance(question, Mapping):
            raise JevProtocolError("questions must be a nonempty named mapping")
        kind = question.get("type")
        if kind not in {"choice", "noul", "score"}:
            raise JevProtocolError("question type must be choice, noul, or score")
        if kind in {"choice", "score"} and not question.get("criteria"):
            raise JevProtocolError("choice and score questions require criteria")


def _validated_response(payload: Any, questions: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
        raise JevProtocolError("response is missing an exact model")
    raw_answers = payload.get("answers")
    usage = payload.get("usage")
    if not isinstance(raw_answers, dict) or set(raw_answers) != set(questions):
        raise JevProtocolError("response answers do not match the question set")
    if not isinstance(usage, dict):
        raise JevProtocolError("response usage is missing")
    for token_key in ("input_tokens", "output_tokens"):
        value = usage.get(token_key)
        if not isinstance(value, int) or value < 0:
            raise JevProtocolError("response usage is invalid")
    for name, answer in raw_answers.items():
        if not isinstance(answer, dict) or answer.get("type") not in {"choice", "noul", "score"}:
            raise JevProtocolError("response contains an invalid answer")
        if answer["type"] != questions[name].get("type"):
            raise JevProtocolError("response answer type does not match its question")
        if answer["type"] == "noul":
            if not isinstance(answer.get("noul"), (int, float)) or not 0 <= answer["noul"] <= 1:
                raise JevProtocolError("noul answer is invalid")
        else:
            probabilities = answer.get("probabilities")
            confidence = answer.get("confidence")
            if not isinstance(probabilities, dict) or not probabilities:
                raise JevProtocolError("answer probabilities are invalid")
            if not all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in probabilities.values()):
                raise JevProtocolError("answer probabilities are invalid")
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise JevProtocolError("answer confidence is invalid")
            if answer["type"] == "choice" and not isinstance(answer.get("choice"), str):
                raise JevProtocolError("choice answer is invalid")
            if answer["type"] == "score" and not isinstance(answer.get("score"), (int, float)):
                raise JevProtocolError("score answer is invalid")
    return payload


class JevProvider:
    def __init__(
        self,
        config: JevConfig,
        api_key: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._api_key = api_key
        self._transport = transport
        self._sleep = sleep

    def capability(self) -> Dict[str, Any]:
        configured = bool(self._api_key)
        return {
            "provider": "typesafe-jev",
            "mode": self.config.mode,
            "model": self.config.model if self.config.mode != "disabled" else None,
            "configured": configured,
            "available": self.config.mode != "disabled" and configured,
            "live_invocation": self.config.mode in {"assist", "active"} and configured,
        }

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": "Bearer " + str(self._api_key),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "BitCadence/0.5.0",
        }

    def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> httpx.Response:
        if not self._api_key:
            raise JevProtocolError("provider is not configured")
        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(
                    base_url=BASE_URL,
                    timeout=self.config.timeout_seconds,
                    transport=self._transport,
                ) as client:
                    response = client.request(method, path, headers=self._headers(), json=json_body)
            except httpx.TimeoutException as exc:
                last_error = exc
                retryable = True
            except httpx.HTTPError as exc:
                last_error = exc
                retryable = True
            else:
                if response.status_code < 400:
                    return response
                last_error = response
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable:
                    return response
            if not retryable or attempt >= self.config.max_retries:
                if isinstance(last_error, Exception):
                    raise last_error
                return last_error
            self._sleep(min(0.1 * (2 ** attempt), 0.5))
        raise AssertionError("bounded retry loop exhausted")

    def health(self) -> Dict[str, Any]:
        if self.config.mode == "disabled":
            return {**self.capability(), "ok": True, "detail": "Jev is disabled"}
        if not self._api_key:
            return {**self.capability(), "ok": False, "detail": "Jev credential is not configured"}
        started = time.monotonic()
        try:
            response = self._request("GET", MODELS_PATH)
            latency_ms = round((time.monotonic() - started) * 1000)
            if response.status_code in (401, 403):
                return {**self.capability(), "ok": False, "detail": "Authentication rejected", "latency_ms": latency_ms}
            if response.status_code == 429:
                return {**self.capability(), "ok": False, "detail": "Rate limited", "latency_ms": latency_ms}
            if response.status_code >= 400:
                return {**self.capability(), "ok": False, "detail": "Provider unavailable", "latency_ms": latency_ms}
            body = response.json()
            models = body.get("models") if isinstance(body, dict) else None
            if not isinstance(models, list) or not all(isinstance(m, dict) and isinstance(m.get("name"), str) for m in models):
                raise JevProtocolError("model discovery response is invalid")
            names = [m["name"] for m in models]
            return {
                **self.capability(),
                "ok": self.config.model in names,
                "detail": "Connection OK" if self.config.model in names else "Configured model is unavailable",
                "latency_ms": latency_ms,
                "models": names,
            }
        except httpx.TimeoutException:
            return {**self.capability(), "ok": False, "detail": "Timed out reaching Jev", "latency_ms": None}
        except (httpx.HTTPError, ValueError, JevProtocolError):
            return {**self.capability(), "ok": False, "detail": "Provider response failed validation", "latency_ms": None}

    def decide(
        self,
        *,
        use_case_id: str,
        question_set_version: str,
        state: Any,
        questions: Mapping[str, Any],
    ) -> DecisionReceipt:
        _validate_questions(questions)
        if not use_case_id or not question_set_version:
            raise JevProtocolError("use_case_id and question_set_version are required")
        if self.config.mode == "disabled":
            return _fallback_receipt(self.config, use_case_id, question_set_version, questions, state, "disabled")
        if not self._api_key:
            return _fallback_receipt(
                self.config, use_case_id, question_set_version, questions, state,
                "fallback", "not_configured",
            )
        started = time.monotonic()
        try:
            response = self._request(
                "POST",
                SYSTEM_ONE_PATH,
                {"state": state, "model": self.config.model, "questions": dict(questions)},
            )
            latency_ms = round((time.monotonic() - started) * 1000)
            if response.status_code in (401, 403):
                error_class = "authentication"
            elif response.status_code == 429:
                error_class = "rate_limit"
            elif response.status_code >= 500:
                error_class = "provider_unavailable"
            elif response.status_code >= 400:
                error_class = "request_rejected"
            else:
                body = _validated_response(response.json(), questions)
                if self.config.mode in {"assist", "active"} and body["model"] != self.config.model:
                    raise JevProtocolError("response model does not match the pinned model")
                answers = body["answers"]
                probabilities = {}
                confidence = {}
                for name, answer in answers.items():
                    if answer["type"] == "noul":
                        probabilities[name] = {"false": 1.0 - float(answer["noul"]), "true": float(answer["noul"])}
                        confidence[name] = None
                    else:
                        probabilities[name] = {str(k): float(v) for k, v in answer["probabilities"].items()}
                        confidence[name] = float(answer["confidence"])
                usage = body["usage"]
                return DecisionReceipt(
                    use_case_id=use_case_id,
                    question_set_version=question_set_version,
                    question_set_digest=_digest(questions),
                    model=body["model"],
                    state_digest=_digest(state),
                    answers={str(k): dict(v) for k, v in answers.items()},
                    probabilities=probabilities,
                    confidence=confidence,
                    latency_ms=latency_ms,
                    usage={"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]},
                    request_id=response.headers.get("x-typesafe-request-id"),
                    mode=self.config.mode,
                    outcome="shadow" if self.config.mode == "shadow" else "success",
                )
            return _fallback_receipt(
                self.config, use_case_id, question_set_version, questions, state,
                "fallback", error_class, latency_ms,
            )
        except httpx.TimeoutException:
            error_class = "timeout"
        except httpx.HTTPError:
            error_class = "connection"
        except (ValueError, JevProtocolError):
            error_class = "invalid_response"
        return _fallback_receipt(
            self.config,
            use_case_id,
            question_set_version,
            questions,
            state,
            "fallback",
            error_class,
            round((time.monotonic() - started) * 1000),
        )


def config_from_manager(config: Any) -> JevConfig:
    return JevConfig(
        mode=str(config.get("MCO_JEV_MODE", "disabled") or "disabled").strip().lower(),
        model=str(config.get("MCO_JEV_MODEL", "jev-latest") or "jev-latest").strip(),
        timeout_seconds=float(config.get("MCO_JEV_TIMEOUT_SECONDS", 5) or 5),
        max_retries=int(config.get("MCO_JEV_MAX_RETRIES", 1) or 0),
    )


def build_provider(
    config: Any,
    db: Any = None,
    org_id: str = "default",
    transport: Optional[httpx.BaseTransport] = None,
) -> JevProvider:
    resolved = config_from_manager(config)
    if resolved.mode == "disabled":
        return JevProvider(resolved, transport=transport)
    api_key = None
    try:
        api_key = build_secret_vault(config, db).get(secret_ref(org_id))
    except (SecretNotFoundError, VaultError):
        pass
    return JevProvider(resolved, api_key=api_key, transport=transport)
