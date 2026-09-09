"""Data contracts and schemas for the standalone job board."""

from enum import Enum

class JobStatus(str, Enum):
    """Execution state of a Job Board task."""
    WAITING = "waiting"                 # Blocked by dependencies
    NEEDS_APPROVAL = "needs_approval"   # Paused at a human-in-the-loop approval gate
    PENDING = "pending"                 # Ready to be leased/executed
    LEASED = "leased"                   # Claimed by an agent instance
    IN_PROGRESS = "in_progress"         # Being executed by an agent instance
    COMPLETED = "completed"             # Execution completed successfully
    FAILED = "failed"                   # Execution failed (may retry/escalate)
    REJECTED = "rejected"               # Terminal: a human rejected the approval gate
    CANCELLED = "cancelled"             # Terminal: a human called off a not-yet-completed job
    HALTED = "halted"                   # Stopped by an operator; manual retry required

# Statuses a job can be cancelled from (anything not already terminal).
CANCELLABLE_STATUSES = {
    JobStatus.WAITING.value, JobStatus.NEEDS_APPROVAL.value,
    JobStatus.PENDING.value, JobStatus.LEASED.value, JobStatus.IN_PROGRESS.value,
}

# Statuses a job can be reassigned from (it already ran its course unsuccessfully).
REASSIGNABLE_STATUSES = {
    JobStatus.HALTED.value,
    JobStatus.FAILED.value, JobStatus.REJECTED.value, JobStatus.CANCELLED.value,
}

# `archived` is a separate boolean flag, not a status - a job keeps whatever
# status it ended in (completed/failed/rejected/cancelled) and is simply
# hidden from the default board view. Only terminal jobs may be archived so
# in-flight work never silently disappears.
ARCHIVABLE_STATUSES = {
    JobStatus.HALTED.value,
    JobStatus.COMPLETED.value, JobStatus.FAILED.value,
    JobStatus.REJECTED.value, JobStatus.CANCELLED.value,
}
