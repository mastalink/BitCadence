# cos-omarchy

## Purpose
UNVERIFIED: ask Joseph. Registered with role `admin` in the BitCadence gateway registry; currently offline.

## Good at
UNVERIFIED: ask Joseph.

## Not allowed (hard rules)
- Offline. Do not send active production or development jobs to this instance.
- Never grant autonomous administrative access without explicit owner verification.
- Never expose tokens or credentials.

## How to give it a job
Send via `mco_send`:
- `to_role`: `admin`
- `to_instance`: `cos-omarchy`
- `title`: Operational status inquiry
- `instructions`: Administrative instructions approved by Joseph
- `priority`: 0

## Job template and worked example
```json
{
  "to_role": "admin",
  "to_instance": "cos-omarchy",
  "title": "Status check for legacy admin instance",
  "instructions": "Report configuration state and role assignment if brought online by owner instruction.",
  "priority": 0
}
```

## What it needs in the job
- Direct owner instruction to reactivate.

## Signs it is stuck and the fix
- **Signs:** Shows offline in registry (normal state).
- **Fix:** Keep offline unless Joseph explicitly directs reactivation.

## Who reviews its work
Joseph (owner).
