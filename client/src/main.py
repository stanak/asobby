from __future__ import annotations

import asyncio
import os
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from time import sleep

import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem

from controller import Controller
from services import Post, edit_post_settings, NET_BATTLE, __version__

LOG_PATH = Path("asobby.log")
LOG_MAX_BYTES = 256 * 1024

# トレイアイコンの状態色
COLOR_IDLE = (128, 128, 128)     # 待機中
COLOR_RECRUIT = (46, 160, 67)    # 募集中
COLOR_BATTLE = (219, 109, 40)    # 対戦中


def make_icon_image(color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=color + (255,))
    d.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 230))
    return img


class TrayApp:
    """タスクトレイ常駐の自動投稿エージェント & ツールランチャー。

    tkinter (ダイアログ類) はメインスレッドで動かし、pystray は
    run_detached、asyncio ループは別スレッドで回す。tkinter を
    ワーカースレッドで動かすと Windows でキー入力が効かないため。
    """

    def __init__(self) -> None:
        self.icon: pystray.Icon | None = None
        self.tk_root = None  # run() で作る tkinter のルート (非表示)
        self.post: Post = Post()

        self._rotate_log()

        self.loop = asyncio.new_event_loop()
        self.controller = Controller(self)

        self._icons = {
            "idle": make_icon_image(COLOR_IDLE),
            "recruit": make_icon_image(COLOR_RECRUIT),
            "battle": make_icon_image(COLOR_BATTLE),
        }

    # -----------------
    # Controller sinks
    # -----------------
    def emit_log(self, level: str, text: str) -> None:
        self._append_log(level, text)
        if level in ("warn", "error"):
            self._notify(text)

    def emit_my_post(self, post: Post) -> None:
        self.post = post
        self._refresh_icon()

    def emit_btn_labels(self, d: dict) -> None:
        if self.icon:
            self.icon.update_menu()

    # -----------------
    # log / notify
    # -----------------
    def _rotate_log(self) -> None:
        try:
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
                LOG_PATH.unlink()
        except OSError:
            pass

    def _append_log(self, level: str, text: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {text}\n"
        try:
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def _notify(self, text: str) -> None:
        if self.icon:
            try:
                self.icon.notify(text, title="asobby")
            except Exception:
                pass

    # -----------------
    # icon state
    # -----------------
    def _status_text(self) -> str:
        if not self.post.id:
            return "待機中 - ホストを立てると自動投稿"
        if self.post.net_status == NET_BATTLE:
            return f"対戦中: {self.post.match_status or self.post.addr}"
        return f"募集中: {self.post.addr}"

    def _refresh_icon(self) -> None:
        if not self.icon:
            return
        if not self.post.id:
            key = "idle"
        elif self.post.net_status == NET_BATTLE:
            key = "battle"
        else:
            key = "recruit"
        self.icon.icon = self._icons[key]
        self.icon.title = f"asobby v{__version__} - {self._status_text()}"
        self.icon.update_menu()

    # -----------------
    # menu actions
    # -----------------
    def _open_lobby(self) -> None:
        webbrowser.open(self.controller.lobby_url())

    def _on_tk(self, fn) -> None:
        """tkinter メインスレッドで fn を実行する。"""
        if self.tk_root is not None:
            self.tk_root.after(0, fn)

    def _open_settings(self) -> None:
        current = self.controller.config_mgr.get_post_defaults()

        def apply(result: dict) -> None:
            for key, value in result.items():
                self.controller.config_mgr.set_post_default(key, value)
            self.controller.update_my_post(
                rank=result["rank"],
                comment=result["comment"],
                stream_url=result["stream_url"],
            )
            if self.icon:
                self.icon.update_menu()

        self._on_tk(lambda: edit_post_settings(self.tk_root, current, apply))

    def _pick_path(self, title: str, callback) -> None:
        """ファイル選択ダイアログを tk メインスレッドで開き、結果を callback に渡す。"""
        def do() -> None:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                parent=self.tk_root,
                title=title,
                filetypes=[("Executable", "*.exe"), ("All Files", "*.*")],
            )
            callback(path or None)
        self._on_tk(do)

    def _open_log(self) -> None:
        if LOG_PATH.exists():
            os.startfile(str(LOG_PATH.resolve()))  # noqa: S606 (Windows 専用)

    def _tool_label(self, tool_name: str) -> str:
        return self.controller.tool_mgr.button_label(tool_name)

    def _handle_tool(self, tool_name: str, title: str) -> None:
        entry = self.controller.tool_mgr.get(tool_name)
        if entry.state.name == "NO_PATH" and not entry.is_active:
            def on_picked(path: str | None) -> None:
                if path:
                    self.controller.tool_mgr.set_path(tool_name, path)
                if self.icon:
                    self.icon.update_menu()
            self._pick_path(title, on_picked)
            return
        elif tool_name == "soku":
            if entry.state.name == "LOADED" and entry.is_active:
                self.controller.tool_mgr.kill_hisoutensoku()
                sleep(0.5)
                self.controller.tool_mgr.load(tool_name)
                self.controller.tool_mgr.reset_state()
            elif entry.state.name == "NO_PATH" and entry.is_active:
                self.controller.tool_mgr.kill_hisoutensoku()
                self.controller.tool_mgr.reset_state()
            elif entry.state.name == "READY":
                self.controller.tool_mgr.load(tool_name)
        else:
            if entry.state.name == "READY":
                self.controller.tool_mgr.load(tool_name)
        if self.icon:
            self.icon.update_menu()

    def _discord_label(self) -> str:
        if self.controller.is_logged_in():
            name = self.controller.discord_user or "?"
            return f"ログアウト ({name})"
        return "Discord でログイン"

    def _discord_action(self) -> None:
        if self.controller.is_logged_in():
            self.controller.logout_discord()
            if self.icon:
                self.icon.update_menu()
            return

        def open_browser(url: str) -> None:
            webbrowser.open(url)

        def done(_fut) -> None:
            if self.icon:
                self.icon.update_menu()

        fut = asyncio.run_coroutine_threadsafe(
            self.controller.login_discord(open_browser), self.loop
        )
        fut.add_done_callback(done)

    def _reset_paths(self) -> None:
        for name in ("autopunch", "giuroll", "soku"):
            self.controller.tool_mgr.clear_path(name)
        self.controller.tool_mgr.reset_state()
        if self.icon:
            self.icon.update_menu()

    def _quit(self) -> None:
        fut = asyncio.run_coroutine_threadsafe(self.controller.close(), self.loop)
        try:
            fut.result(timeout=5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        if self.icon:
            self.icon.stop()
        if self.tk_root is not None:
            self.tk_root.after(0, self.tk_root.destroy)  # mainloop を抜けて終了

    # -----------------
    # menu construction
    # -----------------
    def _comment_menu_items(self):
        """コメント切替サブメニュー (プリセットからラジオ選択)。"""
        current = self.controller.my_post.comment or ""
        presets = self.controller.comment_presets()

        def make_action(text: str):
            def act(icon, item):
                self.controller.set_active_comment(text)
            return act

        yield MenuItem(
            "（なし）",
            make_action(""),
            radio=True,
            checked=lambda item: (self.controller.my_post.comment or "") == "",
        )
        for c in presets:
            label = c if len(c) <= 24 else c[:24] + "…"
            yield MenuItem(
                label,
                make_action(c),
                radio=True,
                checked=lambda item, c=c: self.controller.my_post.comment == c,
            )
        if not presets:
            yield MenuItem("投稿設定でコメントを追加できます", None, enabled=False)

    def _build_menu(self) -> Menu:
        return Menu(
            MenuItem(lambda item: self._status_text(), None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("ロビーページを開く", lambda: self._open_lobby(), default=True),
            MenuItem("投稿設定...", lambda: self._open_settings()),
            MenuItem("コメント切替", Menu(lambda: self._comment_menu_items())),
            MenuItem(lambda item: self._discord_label(), lambda: self._discord_action()),
            Menu.SEPARATOR,
            MenuItem(lambda item: self._tool_label("autopunch"),
                     lambda: self._handle_tool("autopunch", "Select autopunch exe")),
            MenuItem(lambda item: self._tool_label("giuroll"),
                     lambda: self._handle_tool("giuroll", "Select giuroll exe")),
            MenuItem(lambda item: self._tool_label("soku"),
                     lambda: self._handle_tool("soku", "Select th123.exe")),
            MenuItem("ツールのパスをリセット", lambda: self._reset_paths()),
            Menu.SEPARATOR,
            MenuItem("ログを開く", lambda: self._open_log()),
            MenuItem("終了", lambda: self._quit()),
        )

    # -----------------
    # startup
    # -----------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)

        async def startup() -> None:
            await self.controller.sync_initial()
            asyncio.ensure_future(self.controller.detector_loop())
            asyncio.ensure_future(self.controller.api_loop())

        self.loop.create_task(startup())
        self.loop.run_forever()

    def run(self) -> None:
        from tkinter import Tk

        # tkinter はメインスレッドで動かす (ダイアログのキー入力のため)
        self.tk_root = Tk()
        self.tk_root.withdraw()

        threading.Thread(target=self._run_loop, daemon=True, name="asyncio-loop").start()

        self.icon = pystray.Icon(
            "asobby",
            icon=self._icons["idle"],
            title=f"asobby v{__version__} - {self._status_text()}",
            menu=self._build_menu(),
        )
        self._append_log("info", f"asobby agent v{__version__} started")
        self._append_log("info", f"Lobby page: {self.controller.lobby_url()}")
        self.icon.run_detached()
        self.tk_root.mainloop()


if __name__ == "__main__":
    TrayApp().run()
