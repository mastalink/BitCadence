"""Desktop process ownership includes creation time to reject recycled PIDs."""
import json
import os
from pathlib import Path
import psutil
from mco.agentd.platform.windows import WindowsAdapter


class DesktopWindowsAdapter(WindowsAdapter):
    def _record_pid(self, pid, command):
        process = psutil.Process(pid)
        record = {"pid": pid, "command": str(Path(command).resolve()), "created": process.create_time()}
        self.pidfile.parent.mkdir(parents=True, exist_ok=True)
        with self.pidfile.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def reap_orphans(self, pidfile):
        from .controller import stop_tree
        killed = []
        for record in self._read_pidfile(pidfile):
            try:
                process = psutil.Process(int(record["pid"]))
                if (abs(process.create_time() - float(record["created"])) < .01
                        and Path(process.exe()).resolve() == Path(record["command"]).resolve()):
                    stop_tree(process)
                    killed.append(process.pid)
            except (KeyError, ValueError, TypeError, psutil.Error):
                continue
        self._write_records(pidfile, [])
        return killed
