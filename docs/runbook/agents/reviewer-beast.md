# reviewer-beast

## Purpose
The primary reviewer on Beast (GPT family). Evaluates theology, visual quality, brand alignment, kid safety, and comedy script-room notes.

## Good at
- Catholic theological review (checking that prayers are addressed to God, intercession is orthodox, and miracles are authentic).
- Kid safety reviews (K-5 appropriateness, no violence, no inappropriate tropes).
- Embracing the Sofa brand review (punching up at gurus, never down at Dan, Maya, or Rosa).
- Script-room punch-up notes and structural critiques.

## Not allowed (hard rules)
- Cannot review its own builds or scripts (must be an independent model family).
- Never edits code or master files directly; writes reports only.
- Does not approve production releases or spend money.
- Never prints secrets.

## How to give it a job
Send via `mco_send`:
- `to_role`: `reviewer`
- `to_instance`: `reviewer-beast`
- `title`: Explicit review gate task (theology, visual, brand, or script)
- `instructions`: Path to artifact, sha256 checksum, review rubric, and target report path
- `priority`: 3 to 4

## Job template and worked example
```json
{
  "to_role": "reviewer",
  "to_instance": "reviewer-beast",
  "title": "Theology and kid-safety script review for Saint Charles Borromeo",
  "instructions": "Review K:\\SaintsStudio\\saints\\2027\\1104_charles-borromeo\\script.json (sha256: 7f83a1b...). Verify: prayers addressed to God, intercession only, no banned claims, suitable for K-5. Write full report to reviews\\theology_review.json. If minor sourcing doubts exist, list them in owner_confirm rather than failing the gate.",
  "priority": 3
}
```

## What it needs in the job
- Exact file path on Beast (`K:\...` or `C:\AI\...`).
- File sha256 checksum to prevent stale reviews.
- Output report file path.
- Specific gate criteria.

## Signs it is stuck and the fix
- **Signs:** Jobs backlog untaken (e.g. `9 job(s) addressed to it untaken for up to 272 min`).
- **Fix:** Check runner in `C:\Users\masta\.mco\bin\reviewer-beast-run.cmd`. Check OpenAI API quota. Restart the supervised worker process.

## Who reviews its work
Video Shepherd tool (`shepherd.py` validates sha256 and scores); Chief `claude-desktop` and the owner.
