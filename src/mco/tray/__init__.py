"""Optional tray / menu-bar status light for BitCadence.

A status light and a door into the console - not a control panel. Install
with ``pip install 'bitcadence[tray]'``. Headless servers skip this; the
daemon never depends on it running.
"""

from mco.tray.icons import (
    ICON_AMBER,
    ICON_GREEN,
    ICON_GREY,
    ICON_RED,
    icon_state_from_status,
)

__all__ = [
    "ICON_AMBER",
    "ICON_GREEN",
    "ICON_GREY",
    "ICON_RED",
    "icon_state_from_status",
]
