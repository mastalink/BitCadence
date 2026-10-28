# BitCadence AI Fleet Runbooks and Playbooks Index

Central directory of runbooks, operational playbooks, and owner cheat sheets for Joseph's AI crew.

---

## Agent Runbooks (23 Instances)

| Agent File | Role | One-Line Description |
|---|---|---|
| [claude-desktop.md](claude-desktop.md) | `claude` | Chief advisor and orchestrator: routes jobs, interprets policy, and prepares decisions for Joseph. |
| [claude-beast.md](claude-beast.md) | `claude` | Primary media builder on Beast: video assembly, ComfyUI, ElevenLabs voice takes, and build scripts. |
| [claude-code.md](claude-code.md) | `claude` | Primary code builder: develops BitCadence features, Score steps, Blender scripts, and Git PRs. |
| [claude-cio.md](claude-cio.md) | `cio` | Backup Chief and governance officer: approves gated jobs when claude-desktop is offline. |
| [claude-light.md](claude-light.md) | `light` | Lightweight worker (Claude Haiku): fast routine chores, file formatting, and small log checks. |
| [codex-beast.md](codex-beast.md) | `codex` | Code builder on Beast, primary backup media builder, and Saint fact checker. |
| [codex-mac.md](codex-mac.md) | `codex` | macOS code builder on Mac Studio: POSIX scripts, local unit testing, and Mac repository tasks. |
| [mac-studio-codex.md](mac-studio-codex.md) | `codex` | Code builder on Mac Studio: builds and verifies Mac-side media scripts and API integrations. |
| [reviewer-beast.md](reviewer-beast.md) | `reviewer` | Reviewer on Beast (GPT): reviews theology, visual consistency, brand standards, and script room notes. |
| [grok-beast.md](grok-beast.md) | `grok` | Deputy Chief (Grok on Beast): daily fleet status triage, stall intake, Saints research, and comedy script review. |
| [chief-beast.md](chief-beast.md) | `chief` | Watch officer and stall janitor (Codex on Beast): scans queue for hung leases and alerts the Deputy. |
| [antigravity-beast.md](antigravity-beast.md) | `antigravity` | Gemini QA and code reviewer on Beast: audits code diffs, verifies facts, and runs local video QA. |
| [agy-studio-1.md](agy-studio-1.md) | `antigravity` | Gemini reviewer on Mac Studio: watches MP4s, listens to audio, checks deadpan timing, and audits prop design. |
| [agy-studio-2.md](agy-studio-2.md) | `antigravity` | Gemini reviewer on Mac Studio: parallel watch and comedy cut reviewer for motion coverage and audio pauses. |
| [agy-studio-3.md](agy-studio-3.md) | `antigravity` | Gemini reviewer on Mac Studio: audits video pacing, writes runbooks, and checks subtitle synchronization. |
| [agy-studio-4.md](agy-studio-4.md) | `antigravity` | Gemini reviewer on Mac Studio: frame-by-frame visual comedy review, 480p proxy checks, and cast consistency. |
| [muse.md](muse.md) | `muse` | Head comedy writer: writes Embracing the Sofa scripts and the sitcom pilot using the Comic Voice Bible. |
| [stealth-beast.md](stealth-beast.md) | `stealth` | UNVERIFIED: ask Joseph. Registered role `stealth` on Beast in the gateway registry. |
| [via-retrieval-beast.md](via-retrieval-beast.md) | `via-retrieval` | Data collector on Beast: retrieves public Catholic parish schedules and Mass times for the Via app. |
| [via-dispatch-beast.md](via-dispatch-beast.md) | `via-dispatch` | Queue manager: idempotent, UUID-bound dispatch of Via schedule batches to Score. |
| [score-conductor-1.md](score-conductor-1.md) | `conductor` | Score conductor: advances declarative outcome DAGs and enforces evidence verification gates. |
| [local-operator.md](local-operator.md) | `admin` | Local administrative identity on Beast for manual CLI operations and isolated canary test harnesses. |
| [cos-omarchy.md](cos-omarchy.md) | `admin` | UNVERIFIED: ask Joseph. Registered role `admin` in gateway registry; currently offline. |

---

## Production Playbooks (6 Workflows)

| Playbook File | One-Line Description |
|---|---|
| [PLAYBOOK_saints_episode.md](PLAYBOOK_saints_episode.md) | Complete pipeline for Saints Storybook from primary research to scheduled feast-day YouTube upload. |
| [PLAYBOOK_sofa_episode.md](PLAYBOOK_sofa_episode.md) | Production workflow for Embracing the Sofa satirical episodes and multi-lens comedy gates. |
| [PLAYBOOK_sitcom_pilot.md](PLAYBOOK_sitcom_pilot.md) | End-to-end pilot workflow: Muse script, GPT room notes, prop design, locked plates, motion, and voices. |
| [PLAYBOOK_via_data_issue.md](PLAYBOOK_via_data_issue.md) | Investigating public parish schedule API errors, implementing Codex parser fixes, and opening PRs. |
| [PLAYBOOK_incident.md](PLAYBOOK_incident.md) | Incident response procedures for hung leases, gateway outages, leaked tokens, and review disputes. |
| [PLAYBOOK_owner_notes.md](PLAYBOOK_owner_notes.md) | How to translate Joseph's video review notes into rollback stages, builder jobs, and replaced uploads. |

---

## Joseph's Cheat Sheet (The 10 Things He Will Actually Ask)

### 1. "How's it going?" / "Give me a quick status check."
- **What to say:** Ask MosesGrok (`grok-beast`) first to avoid Claude usage. Report in three plain bullets:
  1. What finished in the last 4 hours (with file paths).
  2. What is actively rendering or building right now.
  3. Any failure or blocker and who is fixing it.
- **What to send:** `mco jobs` or `shepherd.py status`.

### 2. "Why is this video not uploaded yet?"
- **What to say:** Give the exact Shepherd stage and blocking gate name (`facts`, `theology`, `visual`, `watch`, or `tech`). If held, state the exact reason from `episode.json`.
- **What to send:** `python shepherd.py next <episode_id>` and report the missing gate or open job.

### 3. "I watched the cut and I have notes on Scene 3."
- **What to say:** "Understood. I will roll back the episode to stage `assemble` (or `motion`), quote your exact notes into a fix job for the builder, and re-run independent reviews before replacing the upload."
- **What to send:** Follow `PLAYBOOK_owner_notes.md`: run `shepherd.py set <id> stage=assemble`, send fix job to `claude-beast`, and upon pass run `shepherd.py replace <id>`.

### 4. "Claude is out of usage / hitting rate limits. Are we stuck?"
- **What to say:** "No. I will switch the build slice to backup builder `codex-beast` on Beast per `BACKUP_BUILDER.md`. It has full access to `K:\` and the scripts. All stand-in cuts receive full independent review plus Gemini audio listening."
- **What to send:** Write `BUILDER_LOCK.txt` and send the stand-in prompt to `to_role: codex, to_instance: codex-beast`.

### 5. "Can we publish this video public right now?"
- **What to say:** "I won't. Standing safety rule: all video uploads are private + scheduled only. Saints upload for feast day 07:00 NY; Sofa uploads on its scheduled slot. You can preview it in YouTube Studio right now."
- **What to send:** Provide the private YouTube video URL from `upload_receipt.json`.

### 6. "Is the prayer in this Saints episode approved?"
- **What to say:** "Every prayer must be signed off by you directly. Check `K:\SaintsStudio\saints\PRAYERS-FOR-OWNER-APPROVAL.md`. It is addressed to God, asks for intercession only, and includes the final sign-off line. Awaiting your word 'approved'."
- **What to send:** Quote the exact 2-3 sentence prayer text directly in chat for his immediate confirmation.

### 7. "An agent is stuck or showing broken on the board. What happened?"
- **What to say:** "The watchdog detected a timeout or unpicked lease. Deputy `grok-beast` has re-dispatched it once. If the worker crashed, the supervisor launcher will restart it. If it fails a second time, it escalates to Chief."
- **What to send:** Check `mco_agents` effective status and `~/.mco/logs/<instance>.log`.

### 8. "A secret or token got committed or showed up in a log!"
- **What to say:** "Rotating the token immediately. I will register a fresh token via `mco register`, revoke the old one, clean local git branches without pushing, and record the event in `docs/runbook/incident-log.md`."
- **What to send:** Run `mco register --name <instance> --role <role>` and verify the exposed key is revoked at the provider.

### 9. "Can I merge this Via data parser or BitCadence PR?"
- **What to say:** "Yes, if the two required conditions are met: (1) independent model review passed with zero defects, and (2) automated tests passed 100%. Only you merge and deploy to production."
- **What to send:** Link to the GitHub PR diff and paste the automated test pass receipt.

### 10. "Can we buy this API plan or try a new paid model service?"
- **What to say:** "Standing hard rule: no spending, new subscriptions, paid API upgrades, or credit purchases without your explicit confirmation. I will test existing local models and available quota first."
- **What to send:** Lay out the free local alternatives on Beast and Mac Studio before presenting costs.
