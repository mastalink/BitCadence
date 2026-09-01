"""Tray icon states and (optional) PIL images.

Four states, and only four:

* green  - daemon reachable, nothing restarting or crash-looped
* amber  - something restarting, backing off, or otherwise degraded
* red    - a worker is crash-looped
* grey   - daemon not running / control API unreachable

Badge overlay (approval count) is applied only when the count is live.
A ``None`` count means the gateway was unreachable - never paint a stale
number as if it were current.
"""

from __future__ import annotations

from typing import Any, Optional

ICON_GREEN = "green"
ICON_AMBER = "amber"
ICON_RED = "red"
ICON_GREY = "grey"

ICON_STATES = (ICON_GREEN, ICON_AMBER, ICON_RED, ICON_GREY)

_AMBER_WORKER_STATES = frozenset({"starting", "backoff", "restarting", "degraded"})
_CRASH_STATES = frozenset({"crashlooped", "crash_looped", "crash-looped"})
_DAEMON_DOWN_STATES = frozenset({"stopped", "down", "not_running", "offline"})

_COLORS = {
    ICON_GREEN: (46, 204, 113, 255),
    ICON_AMBER: (243, 156, 18, 255),
    ICON_RED: (231, 76, 60, 255),
    ICON_GREY: (149, 165, 166, 255),
}


def icon_state_from_status(
    status: Optional[dict[str, Any]],
    daemon_reachable: bool,
) -> str:
    """Pick the tray color from a ``GET /v1/status`` payload.

    ``daemon_reachable`` is the tray's own verdict (could we talk to
    127.0.0.1:18790?). Grey wins over everything when the daemon is gone.
    """
    if not daemon_reachable or status is None:
        return ICON_GREY

    daemon = status.get("daemon")
    daemon_state = _daemon_state(daemon)
    if daemon_state in _DAEMON_DOWN_STATES:
        return ICON_GREY

    workers = status.get("workers") or []
    worker_states = [_worker_state(w) for w in workers]

    if any(s in _CRASH_STATES for s in worker_states):
        return ICON_RED
    if any(s in _AMBER_WORKER_STATES for s in worker_states):
        return ICON_AMBER
    if daemon_state in _AMBER_WORKER_STATES or daemon_state in _CRASH_STATES:
        return ICON_AMBER if daemon_state in _AMBER_WORKER_STATES else ICON_RED
    if status.get("gateway_reachable") is False:
        return ICON_AMBER
    return ICON_GREEN


def should_show_approval_badge(approval_count: Optional[int]) -> bool:
    """True only for a live, non-zero count. ``None`` is 'unknown', not zero."""
    return approval_count is not None and approval_count > 0


def tooltip_text(icon_state: str, approval_count: Optional[int]) -> str:
    """Tooltip. Approval count appears only when the gateway answered."""
    health = {
        ICON_GREEN: "healthy",
        ICON_AMBER: "degraded",
        ICON_RED: "crash-looped",
        ICON_GREY: "daemon not running",
    }.get(icon_state, icon_state)
    if not should_show_approval_badge(approval_count):
        return f"BitCadence: {health}"
    noun = "job" if approval_count == 1 else "jobs"
    return f"BitCadence: {health} · {approval_count} {noun} awaiting approval"


def render_icon(icon_state: str, approval_count: Optional[int] = None, size: int = 64):
    """Build a PIL image for pystray. Requires Pillow (the ``tray`` extra)."""
    from PIL import Image, ImageDraw, ImageFont

    color = _COLORS.get(icon_state, _COLORS[ICON_GREY])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = max(2, size // 16)
    draw.ellipse((pad, pad, size - pad - 1, size - pad - 1), fill=color)

    if should_show_approval_badge(approval_count):
        _draw_badge(draw, size, approval_count)
        try:
            _draw_badge_text(draw, size, approval_count, ImageFont)
        except Exception:
            pass
    return image


def _draw_badge(draw, size: int, count: int) -> None:
    r = max(8, size // 4)
    box = (size - r * 2 - 1, 1, size - 1, r * 2 + 1)
    draw.ellipse(box, fill=(192, 57, 43, 255))


def _draw_badge_text(draw, size: int, count: int, ImageFont) -> None:
    label = "9+" if count > 9 else str(count)
    r = max(8, size // 4)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    # Center in the badge circle as best we can with the default bitmap font.
    cx = size - r - 1
    cy = r + 1
    draw.text((cx - 3, cy - 5), label, fill=(255, 255, 255, 255), font=font)


def _worker_state(worker: Any) -> str:
    if not isinstance(worker, dict):
        return ""
    return str(worker.get("state") or "").strip().lower()


def _daemon_state(daemon: Any) -> str:
    if isinstance(daemon, dict):
        return str(daemon.get("state") or daemon.get("status") or "").strip().lower()
    if isinstance(daemon, str):
        return daemon.strip().lower()
    return ""
