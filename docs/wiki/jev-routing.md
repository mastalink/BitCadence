# Jev routing and its limits

Jev supplies bounded routing advice. It does not approve work, grant permissions, choose a numeric spend quota, or change your running session's model. Deterministic code and existing human gates retain those decisions.

## Ask for a Claude Code tier recommendation

```bash
bitcadence jev route-model --task "Review a documentation change"
```

**Expected:** JSON describing a suggested Claude Code tier and routing outcome. This particular CLI command suggests a tier such as haiku, sonnet or opus; it is not a universal model picker and does not launch a worker.

Add bounded context and choose the fallback tier when useful:

```bash
bitcadence jev route-model --task "Review a documentation change" --context "Only the public user guides changed" --deterministic-tier sonnet
```

Read the returned **outcome, tier and reason**. An exit code of zero does not prove a model answered: disabled, unconfigured, network and timeout cases can return a deterministic fallback successfully. The default fallback is sonnet. [All options](mco-cli-reference.md#cli-mco-jev-route-model).

## Configure the provider

Provider configuration is an administrator task. Follow the [Jev provider setup guide](jev-provider-setup.md) for complete PowerShell instructions and credential handling. The server stores the dedicated API key through its encrypted vault; do not put it in browser code, screenshots or support messages.

| Endpoint | Purpose |
|---|---|
| GET /api/jev | Read sanitized provider metadata, never the secret |
| PUT /api/jev | Set mode, exact model, timeout, retries and optionally the key using admin authorization |
| POST /api/jev/test | Test provider model discovery; this can make an external request |

There is **no dedicated console task-routing test screen or Jev key form** in this inspected version. Generic settings expose some provider fields, but are not the complete connection procedure. Do not look for the old wiki's “Settings → LLM Connections / Routing” walkthrough; that screen was not implemented in the inspected routes.

## Understand the modes

- **Disabled:** no provider request; use deterministic behavior.
- **Shadow:** evaluate advice without applying it. The moving `jev-latest` alias is for shadow evaluation.
- **Assist / active:** require a pinned exact model and still obey the deterministic use-case contract. A mode name does not give the provider permission to execute effects.

Annotation-only paths remain annotation-only even when the global mode is active. A receipt records a judgment, not permission. The [provider contract](jev-provider-setup.md) explains supported use cases, receipts and fallback behavior.

## Codex task routing

The agent-facing MCP tool `mco_jev_route` is distinct from the CLI tier recommendation. It accepts the current task, short context, current model, verified five-hour/weekly remaining percentages, and context pressure. Its result can recommend an execution shape and prompt improvements. It cannot switch the already-running root model or override policy, budgets, approvals or grants.

Missing usage is unknown, not zero and not unlimited. A provider failure must not block ordinary work. Preserve the original task and use a clear deterministic fallback.

## If the recommendation is missing or surprising

1. Read the returned outcome and reason before treating it as a successful provider answer.
2. Check the provider's sanitized configuration and mode with the administrator procedure above.
3. If desired and authorized for an external provider request, run the explicit connection test. Passing discovery is not proof of a real judgment or a completed worker task.
4. Make the task description concrete: target, relevant context, constraints and what completion means. Avoid sending secrets or unnecessary personal data.
5. Inspect the exact model requested and the model reported by the receipt. Do not substitute a different model silently.

To disable future provider requests, use the documented admin configuration endpoint to set mode to disabled. That does not erase the encrypted key or approve any pending work.
