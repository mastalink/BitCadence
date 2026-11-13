"""One window and tray icon for the local BitCadence stack."""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import webbrowser

from .controller import DesktopController


def make_icon():
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), "#122238")
    draw = ImageDraw.Draw(image)
    for x, height in ((14, 20), (26, 38), (38, 28), (50, 46)):
        draw.rounded_rectangle((x - 4, 32 - height // 2, x + 3, 32 + height // 2), radius=3, fill="#52dfc1")
    return image


class DesktopApp:
    def __init__(self, controller):
        self.controller = controller
        self.messages = queue.Queue()
        self.commands = queue.Queue()
        self.closing = False
        self.busy = False
        self.root = tk.Tk()
        self.root.title("BitCadence — Local Control")
        self.root.geometry("980x680")
        self.root.minsize(780, 520)
        from PIL import ImageTk
        self.icon_image = ImageTk.PhotoImage(make_icon())
        self.root.iconphoto(True, self.icon_image)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=(12, 8))
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        style.configure("TLabel", font=("Segoe UI", 10))
        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="BitCadence", font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Your local server and agents, in one place.").pack(anchor="w", pady=(0, 16))
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x")
        self.action_buttons = []
        for label, action in (("Start all", controller.start_all), ("Stop all", controller.stop_all),
                              ("Restart all", self.restart_all)):
            button = ttk.Button(toolbar, text=label, command=lambda fn=action: self.submit(fn))
            button.pack(side="left", padx=(0, 8))
            self.action_buttons.append(button)
        ttk.Button(toolbar, text="Open console", command=lambda: webbrowser.open(controller.url + "/console")).pack(side="right")
        self.status = tk.StringVar(value="Checking local processes…")
        ttk.Label(frame, textvariable=self.status).pack(anchor="w", pady=12)
        self.table = ttk.Treeview(frame, columns=("state", "pid", "error"), height=8, selectmode="browse")
        self.table.bind("<<TreeviewSelect>>", self.update_selection_actions)
        self.table.heading("#0", text="Component")
        self.table.column("#0", width=180)
        for column, title, width in (("state", "Status", 165), ("pid", "Process", 115), ("error", "Last error", 330)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width)
        self.table.pack(fill="x")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=10)
        self.selected_buttons = []
        for label, action in (("Start selected", "start"), ("Stop selected", "stop"), ("Restart selected", "restart")):
            button = ttk.Button(actions, text=label, command=lambda op=action: self.selected_action(op))
            button.pack(side="left", padx=(0, 6))
            self.action_buttons.append(button)
            self.selected_buttons.append(button)
            button.state(["disabled"])
        ttk.Button(actions, text="Fleet settings", command=self.edit_fleet).pack(side="right")
        button = ttk.Button(actions, text="Apply fleet changes…", command=self.review_reload)
        button.pack(side="right", padx=6)
        self.action_buttons.append(button)
        migration = ttk.Button(frame, text="Move workers into app…", command=self.review_handoff)
        migration.pack(anchor="w", pady=(0, 8))
        self.action_buttons.append(migration)
        ttk.Label(frame, text="Live logs • select a component to filter").pack(anchor="w")
        self.log = tk.Text(frame, height=12, background="#122238", foreground="#dce8f4", font=("Consolas", 9), state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(6, 10))
        ttk.Label(frame, text="Stop all also stops matching external components and disables their scheduled tasks.").pack(anchor="w")
        ttk.Label(frame, text="Closing keeps the app in the tray when available. Exit stops only processes started by this app.").pack(anchor="w")
        self.tray = None
        try:
            import pystray
            self.tray = pystray.Icon("BitCadence", make_icon(), "BitCadence — Local Control", pystray.Menu(
                pystray.MenuItem("Open BitCadence", lambda *_: self.messages.put(("show", None)), default=True),
                pystray.MenuItem("Start all", lambda *_: self.messages.put(("action", controller.start_all))),
                pystray.MenuItem("Stop all", lambda *_: self.messages.put(("action", controller.stop_all))),
                pystray.MenuItem("Open console", lambda *_: webbrowser.open(controller.url + "/console")),
                pystray.MenuItem("Exit", lambda *_: self.messages.put(("exit", None)))))
            self.tray.run_detached()
        except Exception as exc:
            self.status.set(f"Tray unavailable: {exc}. Closing the window will exit.")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        threading.Thread(target=self.worker, daemon=True, name="BitCadence-control").start()
        self.root.after(100, self.pump)

    def restart_all(self):
        self.controller.stop_all()
        self.controller.start_all()

    def update_selection_actions(self, _event=None):
        enabled = bool(self.table.selection()) and not self.busy and not self.closing
        for button in self.selected_buttons:
            button.state(["!disabled"] if enabled else ["disabled"])

    def review_handoff(self):
        if self.busy or self.closing:
            return
        try:
            plan = self.controller.preview_handoff()
        except Exception as exc:
            messagebox.showerror("Cannot review handoff", str(exc), parent=self.root)
            return
        components = "\n".join(f"• {item['name']}: {item['state']} (processes: {', '.join(item['pids']) or 'none'})"
                               for item in plan['components'])
        tasks = "\n".join("• " + task for task in plan['tasks']) or "None enabled."
        message = ("Move and restart workers?\n\nThis disables the recurring tasks below, stops the configured component trees "
                   "(including matching external processes), then starts them under this app. Active work may be interrupted. "
                   "Wait for current jobs to finish before proceeding.\n\nComponents:\n" + components +
                   "\n\nScheduled tasks to disable:\n" + tasks + "\n\nNothing has changed yet. Scope is checked again when applied.")
        if messagebox.askokcancel("Move and restart workers", message, icon="warning", parent=self.root):
            self.submit(self.controller.take_control)

    def review_reload(self):
        if self.busy or self.closing:
            return
        try:
            plan = self.controller.preview_reload()
        except Exception as exc:
            messagebox.showerror("Cannot review fleet changes", str(exc), parent=self.root)
            return
        message = ("Apply fleet changes?\n\nRemoved and changed workers are stopped, including matching external processes. "
                   "Matching scheduled tasks may be disabled. Added workers are configured but not started.\n\n" +
                   "\n".join(f"{label}: {', '.join(plan[key]) or 'none'}" for key, label in
                             (("removed", "Remove and stop"), ("changed", "Update and stop"), ("added", "Add"))) +
                   "\n\nNothing has changed yet. The fleet file is read again when applied.")
        if messagebox.askokcancel("Apply fleet changes", message, icon="warning", parent=self.root):
            self.submit(self.controller.reload)

    def edit_fleet(self):
        path = self.controller.fleet_path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Add your worker definitions below.\n[workers]\n", encoding="utf-8")
        import subprocess
        subprocess.Popen(["notepad.exe", str(path)])

    def selected_action(self, action):
        selected = self.table.selection()
        if not selected:
            return
        name = selected[0]
        def run():
            if action in {"stop", "restart"}:
                self.controller.stop(name)
            if action in {"start", "restart"}:
                self.controller.start(name)
        self.submit(run)

    def submit(self, action):
        if self.busy or self.closing:
            return
        self.busy = True
        self.status.set("Working…")
        for button in self.action_buttons:
            button.state(["disabled"])
        self.commands.put(action)

    def worker(self):
        while True:
            try:
                action = self.commands.get(timeout=2)
            except queue.Empty:
                action = None
            if action:
                try:
                    action()
                    self.messages.put(("done", None))
                except Exception as exc:
                    self.messages.put(("error", str(exc)))
            if self.closing:
                try:
                    self.controller.close()
                    self.messages.put(("closed", None))
                except Exception as exc:
                    self.messages.put(("error", str(exc)))
                    self.closing = False
                if self.closing:
                    return
            try:
                self.messages.put(("rows", (self.controller.rows(), self.controller.ready())))
            except Exception as exc:
                self.messages.put(("error", str(exc)))

    def pump(self):
        exit_request = self.controller.runtime_dir / "exit-request"
        if exit_request.exists():
            exit_request.unlink(missing_ok=True)
            self.exit()
        show_request = self.controller.runtime_dir / "show-window"
        if show_request.exists():
            show_request.unlink(missing_ok=True)
            self.root.deiconify()
            self.root.lift()
        start_request = self.controller.runtime_dir / "start-all-request"
        if start_request.exists() and not self.busy and not self.closing:
            start_request.unlink(missing_ok=True)
            self.submit(self.controller.start_all)
        while not self.messages.empty():
            kind, value = self.messages.get_nowait()
            if kind == "show":
                self.root.deiconify()
                self.root.lift()
            elif kind == "action":
                self.submit(value)
            elif kind == "exit":
                self.exit()
            elif kind == "closed":
                if self.tray:
                    self.tray.stop()
                self.root.destroy()
                return
            elif kind in {"done", "error"}:
                self.busy = False
                for button in self.action_buttons:
                    button.state(["!disabled"])
                self.update_selection_actions()
                if kind == "error":
                    self.status.set(value)
                    # A modal dialog blocks this pump - start requests, tray
                    # actions, everything - until clicked. Hidden in the tray,
                    # nobody clicks it, so the fleet silently stops recovering.
                    if self.root.state() != "withdrawn":
                        messagebox.showerror("BitCadence", value, parent=self.root)
            elif kind == "rows":
                rows, ready = value
                for obsolete in set(self.table.get_children()) - {r[0] for r in rows}:
                    self.table.delete(obsolete)
                for name, *values in rows:
                    if self.table.exists(name):
                        self.table.item(name, values=values)
                    else:
                        self.table.insert("", "end", iid=name, text=name, values=values)
                self.update_selection_actions()
                if not self.busy:
                    self.status.set(f"Gateway {'ready' if ready else 'offline or not ready'} • {self.controller.url}")
                selected = self.table.selection()
                lines = self.controller.logs.tail(selected[0] if selected else None, lines=100)
                content = "\n".join(lines) or "Logs appear here for processes started by this app. Existing external workers keep their original logs."
                if self.log.get("1.0", "end-1c") != content:
                    self.log.configure(state="normal")
                    self.log.delete("1.0", "end")
                    self.log.insert("1.0", content)
                    self.log.see("end")
                    self.log.configure(state="disabled")
        self.root.after(150, self.pump)

    def hide(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.exit()

    def exit(self):
        self.closing = True
        self.status.set("Stopping managed processes…")
        self.commands.put(lambda: None)

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18789)
    parser.add_argument("--fleet", type=Path)
    parser.add_argument("--quit", action="store_true", help="Close the running manager and its owned processes")
    parser.add_argument("--start-all", action="store_true", help="Start the configured local stack through the desktop supervisor")
    parser.add_argument("--minimized", action="store_true", help="Start in the notification area when tray support is available")
    args = parser.parse_args()
    if args.quit:
        marker = Path.home() / ".mco" / "desktop" / "exit-request"
        if marker.parent.exists():
            marker.touch()
        return
    if os.name != "nt":
        raise SystemExit("The desktop manager currently supports Windows.")
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BitCadence.LocalControl")
    controller = None
    try:
        kwargs = {"port": args.port}
        if args.fleet:
            kwargs["fleet_path"] = args.fleet
        controller = DesktopController(**kwargs)
        app = DesktopApp(controller)
        if args.start_all:
            app.root.after(0, lambda: app.submit(controller.start_all))
        if args.minimized and app.tray:
            app.root.after(0, app.root.withdraw)
        app.run()
    except Exception as exc:
        from mco.agentd.platform.windows import SupervisorAlreadyRunning
        if isinstance(exc, SupervisorAlreadyRunning):
            marker = Path.home() / ".mco" / "desktop" / "show-window"
            if marker.parent.exists():
                if args.start_all:
                    (marker.parent / "start-all-request").touch()
                marker.touch()
                return
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("BitCadence", str(exc), parent=root)
        root.destroy()
    finally:
        if controller:
            controller.close()


if __name__ == "__main__":
    main()
