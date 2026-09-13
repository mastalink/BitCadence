# Voiceover scripts

Editable narration for the five actual recordings. Times are cue targets in seconds. Use a calm, conversational voice. Say BitCadence as 'Bit Cadence', AWS as its letters, and S3 as 'S three'. No voice cloning is required.

The local preview generator uses Windows' synthetic voice without a paid API. Originals remain unchanged. Review the preview before investor use.

## Approval before execution

Approval before execution

[00s]
This is Bit Cadence running in our AWS test environment. The important question is simple: who decides when an automated worker is allowed to act?

[18s]
Here, we create a harmless job and require human approval. The job waits. Being assigned to a worker does not give that worker permission to skip the approval step.

[43s]
The operator can inspect the request before releasing it. Once approved, the cloud worker picks up the job and performs a checksum calculation.

[62s]
The job completes, and its history records approval and execution. This demonstrates human control over real cloud work. It is a control demonstration, not a claim about artificial intelligence reasoning.

## The stop control

The stop control

[00s]
This shorter recording introduces the stop control in the live console. The operator enables Stop work and saves the setting.

[15s]
The setting pauses intake and governs active attempts. This clip shows the control being saved; our separate active kill switch demo shows a running job being halted.

[29s]
Turning the control off allows new work again. It does not automatically retry halted jobs, and it does not shut down the AWS machines.

## Where everything lives

Where everything lives

[00s]
Think of this cloud lab as a small supervised team. Your computer is the operator's desk. A private tunnel connects your browser to the hub in AWS.

[17s]
The hub assigns work and checks permission. Two separate cloud machines act as workers. They connect to the hub privately, using separate identities.

[32s]
Audit evidence is stored separately in Amazon S three. Deployments use temporary credentials, and the lab has timed shutdown safeguards.

[44s]
This diagram explains the architecture. It is not a live status display, and this small test environment is not yet a highly available production service.

## One task, two cloud workers

One task, two cloud workers

[00s]
This is a real delegation demonstration in our AWS lab. The operator gives one task to the first worker. That worker calculates a checksum and creates a follow-up task for a separate reviewer.

[24s]
Notice who requested the follow-up: worker lab. The operator did not create this second job. The handoff carries the original job identifier and the worker's result.

[46s]
Delegation does not bypass human control. The review is waiting for approval. We inspect the handoff and release it, allowing the reviewer on the second cloud machine to continue.

[67s]
The reviewer completes the delegated job. The history connects the request, approval, and execution. We have demonstrated controlled delegation across machines, using deterministic work rather than simulated AI reasoning.

## Stop an active attempt

Stop an active attempt

[00s]
A cloud worker has picked up a real job. This harmless test performs small work units for up to three minutes and checks its permission to continue between units.

[28s]
Now the operator enables Stop work and saves the setting. This is the governance control. It is separate from shutting down the cloud machines.

[51s]
The job changes to Stopped by operator. The worker's log confirms that it halted and withheld its result. The stop decision remains visible in the job history.

[70s]
A separate check replayed the old completion claim. The server rejected it with HTTP four oh nine, and the job stayed halted. This proves the cooperative worker and result boundary.
