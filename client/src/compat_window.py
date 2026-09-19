"""Wine control window: no shell tray or WinRT dependency.

Only the Tk thread touches widgets. Background notifications use a queue.
The menu is rebuilt from the same descriptors as the Windows tray.
"""
from __future__ import annotations

from collections import deque
from queue import Empty, Queue
import tkinter as tk
from tkinter import ttk

from pystray import Menu

from i18n import t


class CompatWindow:
    def __init__(self, root, app):
        self.root, self.app = root, app
        self.pending = Queue()
        self.notices = deque(maxlen=50)
        self.popup = None
        root.title("asobby — Wine")
        root.minsize(540, 380)
        root.protocol("WM_DELETE_WINDOW", app._quit)
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        self.hint = ttk.Label(frame, wraplength=520)
        self.hint.pack(fill="x", pady=(0, 8))
        self.status = ttk.Label(frame, wraplength=520)
        self.status.pack(fill="x", pady=(0, 8))
        self.menu_button = ttk.Button(frame, command=self._open_menu)
        self.menu_button.pack(anchor="w", pady=(0, 8))
        self.listbox = tk.Listbox(frame, height=7, exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._selected)
        self.detail = tk.Text(frame, height=5, wrap="word", state="disabled")
        self.detail.pack(fill="x", pady=8)
        self.open_button = ttk.Button(frame, command=self._open_notice, state="disabled")
        self.open_button.pack(anchor="w")
        self._tick()

    def notify(self, text, *, important=False, on_click=None):
        self.pending.put((text, important, on_click))

    def _tick(self):
        self.hint.configure(text=t("wine.window_hint"))
        self.status.configure(text=self.app._status_text())
        self.menu_button.configure(text=t("wine.menu"))
        self.open_button.configure(text=t("wine.open_notification"))
        changed = False
        # Bound each GUI tick, and retain only the newest 50 notifications.
        for _ in range(50):
            try:
                self.notices.appendleft(self.pending.get_nowait())
                changed = True
            except Empty:
                break
        if changed:
            self.listbox.delete(0, "end")
            for text, important, _callback in self.notices:
                self.listbox.insert("end", ("! " if important else "") + text.replace("\n", " "))
            self.listbox.selection_set(0)
            self._selected()
        self.root.after(250, self._tick)

    def _selected(self, _event=None):
        selected = self.listbox.curselection()
        text, _important, callback = self.notices[selected[0]] if selected else ("", False, None)
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", text)
        self.detail.configure(state="disabled")
        self.open_button.configure(state="normal" if callback else "disabled")

    def _open_notice(self):
        selected = self.listbox.curselection()
        if selected:
            callback = self.notices[selected[0]][2]
            if callback:
                self._invoke(callback)

    def _invoke(self, callback):
        try:
            callback()
        except Exception as exc:
            self.app.emit_log("error", f"Wine window action failed: {exc!r}")
            self.notify(t("wine.action_failed"), important=True)

    def _make_menu(self, parent, source):
        menu = tk.Menu(parent, tearoff=False)
        for item in source:
            if item is Menu.SEPARATOR:
                menu.add_separator()
                continue
            options = {"label": item.text, "state": "normal" if item.enabled else "disabled"}
            if item.submenu is not None:
                menu.add_cascade(**options, menu=self._make_menu(menu, item.submenu))
            else:
                if item.checked is not None:
                    options["label"] = ("✓ " if item.checked else "    ") + item.text
                menu.add_command(**options, command=lambda item=item: self._invoke(lambda: item(self.app.icon)))
        return menu

    def _open_menu(self):
        if self.popup is not None:
            self.popup.destroy()
        self.popup = self._make_menu(self.root, self.app._build_menu())
        try:
            self.popup.tk_popup(self.menu_button.winfo_rootx(),
                                self.menu_button.winfo_rooty() + self.menu_button.winfo_height())
        finally:
            self.popup.grab_release()
