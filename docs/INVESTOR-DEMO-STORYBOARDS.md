# BitCadence investor demo recording brief

Prepared September 2026. Record three short, separate demonstrations in the order below. Lead with the operator's decision and an observable result. Use synthetic inputs and real product behavior. These are storyboards, not evidence that a recording or cloud acceptance has completed.

## Why these stories

| Buyer concern | Primary research | Implication for the demo |
|---|---|---|
| Keeping people accountable as agents execute | Microsoft's 2026 Work Trend Index describes coordinated changes across employees, leaders, IT and security, including work organized around intent and review. This is vendor research, not proof of demand for BitCadence. [Report](https://www.microsoft.com/en-us/worklab/work-trend-index/agents-human-agency-and-the-opportunity-for-every-organization) | Show the approval boundary working before showing agent output. |
| Being able to investigate and respond when automation fails | NIST's Generative AI Profile recommends defined oversight responsibilities, incident reviews and retained evaluation history (GV-1.5 and GOVERN 3.2). It is voluntary risk guidance, not product certification. [NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf) | Demonstrate a real stop and rejected stale completion, then inspect its evidence. |
| Keeping AI experiments financially understandable | The FinOps Foundation's 2026 survey identifies AI cost management as the leading skill need and highlights visibility, allocation and value measurement difficulties. Its sample represents the FinOps community rather than all businesses. [State of FinOps 2026](https://data.finops.org/) | Show explicit resource and execution limits; distinguish estimates from measured spend. |

Our inference: these concerns make control, recoverability and bounded experimentation a stronger first pitch than a generic multi-agent chat. They do not establish market size, customer traction or willingness to pay.

## Evidence boundary before recording

| Capability | Evidence available at preparation | Recording rule |
|---|---|---|
| Console job creation, approval, deterministic completion | Local browser execution documented in `COMPLETION-2026-09.md` | Can record as a local product demonstration. |
| Stop work, halted status, stale completion rejected with HTTP 409 | Local browser/API probe and acceptance tests documented | Can record on the isolated review instance. |
| Fenced leases, audit chain, signed checkpoint verification | Local SQLite/PostgreSQL tests documented | Label the runtime used; a hash chain alone does not prove absence of tail deletion. |
| Live AWS worker execution | Parent operator reports the first real worker completed; the acceptance probe had an incorrect result-field assertion | Recheck the job and save fresh evidence before calling the cloud result verified. |
| Both cloud spokes, approval-denial probe, Bedrock inference, locked evidence | Lab implementation and acceptance harness exist; full live acceptance is still pending at preparation | Add verified footage only after the current cloud run passes each assertion. |
| Private TLS, scoped instance roles, bounded sessions and stop timers | Defined in `infra/aws/lab/README.md` and lab configuration | Architecture footage must say configured unless matched to fresh deployed-state checks. |

The completion report predates the lab rollout. Its older statement that no cloud resources are asserted should not be treated as today's inventory. Save the current commit, UTC capture time, anonymized job IDs, test outcome and environment beside each recording.

## 1. Approve the work, then watch it happen — 75 seconds

**Audience:** broad investor audience and operational leaders. **Payoff:** useful automation with a visible human decision.

| Time | Picture/action | Narration |
|---|---|---|
| 0–12 s | Job board; create “Draft a customer-support handoff” using synthetic text, a registered worker and approval required. | “A team needs useful work from an agent, with a clear point where a person authorizes execution.” |
| 12–28 s | Show the job remaining in Needs approval while a worker is available. | “The worker cannot start this job before approval.” |
| 28–45 s | Operator approves; show Running and the actual assigned worker. | “The operator approves; the hub assigns the job to the scoped worker.” |
| 45–63 s | Show the real result. Use a short Bedrock summary only after the live inference check passes; otherwise use the real local checksum task and label it a control demonstration. | “This output came from this recorded execution.” |
| 63–75 s | Open job detail/history and point to approval, assignment and completion. | “The result stays connected to who authorized it and which worker performed it.” |

**Capture gate:** prove no execution before approval; preserve the result and history. Do not imply customer messaging, CRM integration, a reviewer quality judgment or multiple collaborating LLMs: those are not established by this demo.

## 2. Stop a job and reject its late result — 85 seconds

**Audience:** technical investors, platform and security buyers. **Payoff:** operational control when work is already underway.

| Time | Picture/action | Narration |
|---|---|---|
| 0–15 s | An isolated, deliberately delayed test job is Running. Label it “Controlled failure drill.” | “Now suppose an operator needs to stop work already in progress.” |
| 15–33 s | Settings → Stop work; return to the job's halted status. | “Stop work pauses acquisition and fences the active attempt.” |
| 33–52 s | Let the test worker submit its saved, now-stale completion proof. Show sanitized real HTTP 409/FENCED output beside the unchanged job. | “The old worker cannot mark this attempt complete after losing authority.” |
| 52–70 s | Show actual audit verification and an externally retained signed checkpoint, or the job history if no checkpoint was prepared. | “The state transition has evidence we can inspect and verify.” |
| 70–85 s | Turn Stop work off; show that the halted job still requires deliberate Retry. | “Resuming intake does not silently restart this halted job.” |

**Capture gate:** run only on an isolated instance; keep the real rejected response. Call this rejection of a stale completion, not reversal of actions already taken. Arbitrary external side effects require cooperative checkpoints and downstream idempotency. Add S3 retention footage only when a version-specific live retention check passes.

## 3. A private cloud lab with clear operating limits — 80 seconds

**Audience:** CTOs and financially minded investors. **Payoff:** a concrete environment to evaluate distributed agents with visible boundaries.

| Time | Picture/action | Narration |
|---|---|---|
| 0–18 s | Infrastructure diagram: SSM operator tunnel → hub → two scoped spokes; separate evidence bucket. | “One private hub coordinates two workers. Each spoke has its own identity and limited access.” |
| 18–36 s | Real cloud console worker list and a successful job; show worker approval-denial evidence if that live check passed. | “Workers execute their assigned work; they do not inherit the operator's approval authority.” |
| 36–55 s | Show deployed instance sizes and verified timer/session configuration, with compact overlays. | “The lab uses small instances, bounded worker sessions and short model outputs, with scheduled shutdown.” |
| 55–68 s | A cost card labeled “estimate”: approximately $0.06/hour while running and roughly $6–8/month retained infrastructure while stopped, subject to current price verification. | “This is an estimated lab envelope, not a spending cap. Stopped machines still leave storage and secrets charges.” |
| 68–80 s | Show the documented start/open/stop path and conclude on the diagram. | “This gives teams a controlled place to evaluate their workflow before designing production scale.” |

**Capture gate:** use fresh AWS inventory and acceptance results. Do not claim a private subnet or no internet egress: public IPv4 provides outbound access, while security groups block inbound internet clients. This is a single-zone lab, not high availability. The persisted data disk is not a backup. Automatic stop is a safeguard, not account-wide budget enforcement.

## Recording priorities

1. Record story 1 first; it communicates value fastest. Use one honest environment label throughout, with visible labels for any local-to-cloud cut.
2. Record story 2 next; the rejected late result is the strongest technical proof. Keep its failure drill isolated from other jobs.
3. Record story 3 after cloud acceptance and inventory checks. Present the architecture as deployed only where verified.

Capture the browser or app only, hide unrelated tabs and notifications, and keep tokens and login callbacks off screen. Keep a raw take alongside the edited 60–90 second clip. Label time jumps so accelerated execution is not mistaken for latency evidence. Never replace a failed assertion with a success overlay. Avoid invented ROI, compliance certification, customer logos or investor endorsements.
