"""Write editable, timed narration scripts for the recorded demonstrations."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "output/playwright/demo-pack"
SCRIPTS = [
    ("01-approval-to-execution", "Approval before execution", 77.32, [
        (0, "This is Bit Cadence running in our AWS test environment. The important question is simple: who decides when an automated worker is allowed to act?"),
        (18, "Here, we create a harmless job and require human approval. The job waits. Being assigned to a worker does not give that worker permission to skip the approval step."),
        (43, "The operator can inspect the request before releasing it. Once approved, the cloud worker picks up the job and performs a checksum calculation."),
        (62, "The job completes, and its history records approval and execution. This demonstrates human control over real cloud work. It is a control demonstration, not a claim about artificial intelligence reasoning.")]),
    ("02-stop-and-resume", "The stop control", 41.24, [
        (0, "This shorter recording introduces the stop control in the live console. The operator enables Stop work and saves the setting."),
        (15, "The setting pauses intake and governs active attempts. This clip shows the control being saved; our separate active kill switch demo shows a running job being halted."),
        (29, "Turning the control off allows new work again. It does not automatically retry halted jobs, and it does not shut down the AWS machines.")]),
    ("03-cloud-infrastructure", "Where everything lives", 53.76, [
        (0, "Think of this cloud lab as a small supervised team. Your computer is the operator's desk. A private tunnel connects your browser to the hub in AWS."),
        (17, "The hub assigns work and checks permission. Two separate cloud machines act as workers. They connect to the hub privately, using separate identities."),
        (32, "Audit evidence is stored separately in Amazon S three. Deployments use temporary credentials, and the lab has timed shutdown safeguards."),
        (44, "This diagram explains the architecture. It is not a live status display, and this small test environment is not yet a highly available production service.")]),
    ("04-delegation", "One task, two cloud workers", 84.68, [
        (0, "This is a real delegation demonstration in our AWS lab. The operator gives one task to the first worker. That worker calculates a checksum and creates a follow-up task for a separate reviewer."),
        (24, "Notice who requested the follow-up: worker lab. The operator did not create this second job. The handoff carries the original job identifier and the worker's result."),
        (46, "Delegation does not bypass human control. The review is waiting for approval. We inspect the handoff and release it, allowing the reviewer on the second cloud machine to continue."),
        (67, "The reviewer completes the delegated job. The history connects the request, approval, and execution. We have demonstrated controlled delegation across machines, using deterministic work rather than simulated AI reasoning.")]),
    ("05-kill-switch", "Stop an active attempt", 87.6, [
        (0, "A cloud worker has picked up a real job. This harmless test performs small work units for up to three minutes and checks its permission to continue between units."),
        (28, "Now the operator enables Stop work and saves the setting. This is the governance control. It is separate from shutting down the cloud machines."),
        (51, "The job changes to Stopped by operator. The worker's log confirms that it halted and withheld its result. The stop decision remains visible in the job history."),
        (70, "A separate check replayed the old completion claim. The server rejected it with HTTP four oh nine, and the job stayed halted. This proves the cooperative worker and result boundary.")]),
]

def main():
    output = ROOT / "narration"
    output.mkdir(exist_ok=True)
    manifest = []
    overview = ["# Voiceover scripts\n", "Editable narration for the five actual recordings. Times are cue targets in seconds. Use a calm, conversational voice. Say BitCadence as 'Bit Cadence', AWS as its letters, and S3 as 'S three'. No voice cloning is required.\n", "The local preview generator uses Windows' synthetic voice without a paid API. Originals remain unchanged. Review the preview before investor use.\n"]
    for stem, title, duration, segments in SCRIPTS:
        manifest.append({"stem": stem, "title": title, "duration": duration,
                         "segments": [{"start": start, "text": text} for start, text in segments]})
        text = title + "\n\n" + "\n\n".join(f"[{start:02d}s]\n{line}" for start, line in segments) + "\n"
        (output / (stem + ".txt")).write_text(text, encoding="utf-8")
        overview.extend([f"## {title}\n", text])
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output / "VOICEOVER-SCRIPTS.md").write_text("\n".join(overview), encoding="utf-8")

if __name__ == "__main__":
    main()
