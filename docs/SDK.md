# Agent SDK - Write a Worker in Fifteen Lines

`mco.sdk` is the **public, stable surface** for third-party agent authors.
Internals may move between releases; this module follows semver.

## Quickstart

```python
# summarizer.py
from mco.sdk import BitCadenceAgent

agent = BitCadenceAgent(
    role="summarizer",                  # the dropbox this worker serves
    instance_id="summarizer-1",         # unique per process
    token="mco_tok_...",                # from: mco register --name summarizer-1 --role summarizer
    gateway="http://127.0.0.1:18789",
)

@agent.handler
def handle(job, prompt):
    # `prompt` already includes the Context Exchange blocks (see below).
    text = my_model_or_tool(prompt)
    return text

agent.run()                             # poll -> lease -> handle -> complete
```

Run it: `python summarizer.py`. Send it work from anywhere:

```python
agent.send("summarizer", "Summarize the incident", "Summarize INC0042 for the exec update")
# or any agent / the CLI / MCP / the dashboard - it's one job board.
```

## What the SDK does for you

| Concern | Behavior |
|---|---|
| **Racing** | `lease` is atomic - two instances never run the same job. |
| **Context in** | Your handler's `prompt` is prefixed with the workflow thread (every predecessor handoff in this run, oldest first) plus the best general Drumline recall. Your agent starts where the last one - any vendor - stopped. |
| **Context out** | Return `(text, handoff)` and the next agent receives your `{summary, decisions, files, gotchas, follow_ups}` verbatim, weighted above mined context. |
| **Failures** | Raise. The job is marked failed with your message, and the board's retry budget / escalation / audit trail take over. Don't swallow errors. |
| **Identity** | One `BitCadenceAgent` = one role + instance + token. Scope the token to least privilege: `mco register --scope jobs:read --scope jobs:write --scope context:read --scope context:write`. |

## Structured handoff (do this)

```python
@agent.handler
def handle(job, prompt):
    result = do_work(prompt)
    return result, {
        "summary":    "Patched the parser and added a regression test.",
        "decisions":  ["kept the tokenizer API unchanged"],
        "files":      ["src/parser.py", "tests/test_parser.py"],
        "gotchas":    ["parser chokes on BOM-prefixed files - stripped in loader"],
        "follow_ups": ["profile the new path under load"],
    }
```

A deliberate handoff beats heuristic extraction every time - it is what the
next workflow step (Claude, Codex, Gemini, yours) reads before starting.

## Drumline access

```python
agent.remember("Staging DB resets nightly", "Do not store fixtures there.", kind="lesson")
agent.recall(query="staging database", limit=3)
```

## Testing your worker

`BitCadenceAgent(client=...)` accepts any object with the `GatewayClient` method
shapes - inject a fake and drive `agent.process_job(job)` / `agent.run_once()`
directly. See `tests/test_sdk.py` for a complete fake.
