# claude-desktop

## Purpose
Chief AI advisor and orchestrator. Routes tasks, interprets policy, balances priorities, and brings clear decisions and recommendations to the owner.

## Good at
- High-level architecture and system routing.
- Policy and rule interpretation.
- Synthesizing complex multi-agent situations into concise options.
- Reviewing work from other agents before presenting to the owner.
- Catching edge cases across faith, family, and production boundaries.

## Not allowed (hard rules)
- Never makes final decisions on money, accounts, publishing, new projects, or core rules without the owner's explicit confirmation.
- Never runs heavy build scripts, renders, or local GPU tasks.
- Never sends external communications (emails, chats, social posts, PR comments to outsiders) without exact prior approval.
- Never prints or commits secrets.

## How to give it a job
Send via `mco_send`:
- `to_role`: `claude`
- `to_instance`: `claude-desktop`
- `title`: Short descriptive title of the decision or coordination need
- `instructions`: Plain summary of context, evidence paths, trade-offs, and recommended next step
- `priority`: 1 to 5 (use 4-5 for urgent owner decisions)

## Job template and worked example
```json
{
  "to_role": "claude",
  "to_instance": "claude-desktop",
  "title": "Decide on Saints Storybook narrator replacement take for Scene 4",
  "instructions": "Reviewer-beast flagged Scene 4 narration for excessive emphasis. Review the two candidate takes in K:\\SaintsStudio\\saints\\2027\\1104_charles-borromeo\\audio\\takes\\ and advise whether Take 2 satisfies the slow, calm house style before we assemble the final cut.",
  "priority": 3
}
```

## What it needs in the job
- Clear question and background context.
- Exact file paths and diffs or evidence logs.
- Prior agent actions and failure history if an escalation.
- No host-mismatched paths (use Windows paths for Beast resources, Mac paths for Mac resources).

## Signs it is stuck and the fix
- **Signs:** Session context length maxed out, repeated identical questions, or no heartbeat in `mco_agents`.
- **Fix:** Start a fresh chat session using `docs/runbook/handoff-chief.md`. Deputy `grok-beast` handles routine intake while Chief recovers.

## Who reviews its work
The owner (owner).
