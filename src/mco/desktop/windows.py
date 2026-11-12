"""Desktop process ownership includes creation time to reject recycled PIDs."""
from mco.agentd.platform.windows import WindowsAdapter


class DesktopWindowsAdapter(WindowsAdapter):
    # The base adapter now records and re-verifies the full identity of every
    # child (start time, user, executable, argv and a supervisor nonce - ADR
    # 0002 R6) and kills the verified process tree, which is a superset of the
    # creation-time check this class used to add.
    pass
