from __future__ import annotations

import asyncio
import os
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from time import sleep

from PIL import Image, ImageDraw
from pystray import Menu, MenuItem

from controller import Controller
from tray_icon import TrayIcon
from services import (
    Post,
    POST_TYPE_LABEL,
    POST_TYPE_OPTIONS,
    edit_post_settings,
    NET_BATTLE,
    __version__,
)
from stats_window import open_stats_window
import toast

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
        self.icon: TrayIcon | None = None
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

    def emit_notify(self, text: str) -> None:
        self._notify(text)
        if self.icon:
            self.icon.update_menu()

    def emit_request(self, req, text: str) -> None:
        def callback(reply: str) -> None:
            fut = asyncio.run_coroutine_threadsafe(
                self.controller.reply_request(req.message_id, reply),
                self.loop,
            )

            def done(_fut) -> None:
                if self.icon:
                    self.icon.update_menu()

            fut.add_done_callback(done)

        shown = toast.show_request_toast(
            text + "\n承諾/拒否をボタンで返信できます",
            callback,
            log=lambda m: self._append_log("warn", m),
        )
        if not shown:
            self._notify(text + "（トレイメニューの「リクエストに返信」から返信できます）")
        if self.icon:
            self.icon.update_menu()

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
    def _post_type_label(self) -> str:
        return POST_TYPE_LABEL.get(self.post.post_type, "カジュアル")

    def _status_text(self) -> str:
        if self.controller.detect_error == "access_denied":
            return "検出済み・メモリ読取不可 (ゲームが管理者権限?)"
        if self.controller.is_detect_paused():
            rest = self.controller.detect_pause_remaining_min()
            return f"自動検知 停止中 (残り約 {rest} 分)"
        if not self.post.id:
            return "待機中 - ホストを立てると自動投稿"
        mode = self._post_type_label()
        if self.post.net_status == NET_BATTLE:
            return f"対戦中 ({mode}): {self.post.match_status or self.post.addr}"
        return f"募集中 ({mode}): {self.post.addr}"

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
                post_type=result["post_type"],
                comment=result["comment"],
                stream_url=result["stream_url"],
            )
            if self.icon:
                self.icon.update_menu()

        self._on_tk(lambda: edit_post_settings(self.tk_root, current, apply))

    def _open_stats(self) -> None:
        self._on_tk(
            lambda: open_stats_window(self.tk_root, self.controller.local_store)
        )

    def _sync_stats(self) -> None:
        """戦績DBのサーバー同期を手動で実行する。結果はトーストで通知される。"""
        fut = asyncio.run_coroutine_threadsafe(
            self.controller.sync_stats_now(), self.loop
        )

        def done(_fut) -> None:
            if self.icon:
                self.icon.update_menu()

        fut.add_done_callback(done)

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

    def _open_update_page(self) -> None:
        if self.controller.update_available:
            _, url = self.controller.update_available
            webbrowser.open(url)

    def _tool_label(self, tool_name: str) -> str:
        return self.controller.tool_mgr.button_label(tool_name)

    def _handle_tool(self, tool_name: str, title: str) -> None:
        entry = self.controller.tool_mgr.get(tool_name)
        # パス未設定ならファイル選択を開く。soku だけは稼働中 (= stop soku
        # 表示) を除外する。giuroll/autopunch は DLL ロード済みでも
        # ラベルが "set X path" なので、そのまま選択できるようにする
        if entry.state.name == "NO_PATH" and (
            tool_name != "soku" or not entry.is_active
        ):
            def on_picked(path: str | None) -> None:
                if path:
                    self.controller.tool_mgr.set_path(tool_name, path)
                if self.icon:
                    self.icon.update_menu()
            self._pick_path(title, on_picked)
            return
        elif tool_name == "soku":
            # ラベルと同じ判定 (button_label 参照): パス設定 + 稼働中なら
            # restart、パス設定 + 未稼働なら load、パス未設定 + 稼働中なら stop
            if entry.state.name == "NO_PATH" and entry.is_active:
                self.controller.tool_mgr.kill_hisoutensoku()
                self.controller.tool_mgr.reset_state()
            elif entry.is_active:
                self.controller.tool_mgr.kill_hisoutensoku()
                sleep(0.5)
                self.controller.tool_mgr.load(tool_name)
                self.controller.tool_mgr.reset_state()
            else:
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
    def _post_type_menu_items(self):
        """募集タイプ切替サブメニュー (カジュアル/ランクマのラジオ選択)。"""
        def make_action(value: str):
            def act(icon, item):
                self.controller.set_active_post_type(value)
            return act

        for label, value in POST_TYPE_OPTIONS:
            yield MenuItem(
                label,
                make_action(value),
                radio=True,
                checked=lambda item, value=value: (
                    (self.controller.my_post.post_type or "casual") == value
                ),
            )

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

    def _stream_menu_items(self):
        """配信URL切替サブメニュー (プリセットからラジオ選択)。"""
        current = self.controller.my_post.stream_url or ""
        presets = self.controller.stream_presets()

        def make_action(text: str):
            def act(icon, item):
                self.controller.set_active_stream(text)
            return act

        yield MenuItem(
            "（なし）",
            make_action(""),
            radio=True,
            checked=lambda item: (self.controller.my_post.stream_url or "") == "",
        )
        for url in presets:
            label = url if len(url) <= 30 else url[:30] + "…"
            yield MenuItem(
                label,
                make_action(url),
                radio=True,
                checked=lambda item, url=url: self.controller.my_post.stream_url == url,
            )
        if not presets:
            yield MenuItem("投稿設定で配信URLを追加できます", None, enabled=False)

    def _pause_menu_items(self):
        """ホスト自動検知の一時停止サブメニュー。"""
        def make_pause(seconds: int):
            def act(icon, item):
                self.controller.pause_auto_detect(seconds)
                if self.icon:
                    self.icon.update_menu()
            return act

        def resume(icon, item):
            self.controller.resume_auto_detect()
            if self.icon:
                self.icon.update_menu()

        paused = self.controller.is_detect_paused()
        if paused:
            rest = self.controller.detect_pause_remaining_min()
            yield MenuItem(f"停止中 (残り約 {rest} 分)", None, enabled=False)
            yield MenuItem("今すぐ再開する", resume)
            yield Menu.SEPARATOR
        yield MenuItem("30 分停止", make_pause(30 * 60))
        yield MenuItem("1 時間停止", make_pause(60 * 60))
        yield MenuItem("3 時間停止", make_pause(3 * 60 * 60))

    def _pause_menu_label(self) -> str:
        if self.controller.is_detect_paused():
            rest = self.controller.detect_pause_remaining_min()
            return f"ホスト自動検知 (停止中 残り約 {rest} 分)"
        return "ホスト自動検知を一時停止"

    def _toggle_copy_addr(self) -> None:
        self.controller.set_copy_addr_enabled(
            not self.controller.copy_addr_enabled()
        )
        if self.icon:
            self.icon.update_menu()

    def _request_menu_items(self):
        """未返信リクエストへの承諾/拒否メニュー。"""
        self.controller._prune_pending_requests()
        for req in self.controller.pending_requests:
            type_label = self.controller._request_type_label(req.req_type)
            header = f"{req.from_name}: {type_label}"

            def make_reply(message_id: str, reply: str):
                def act(icon, item):
                    fut = asyncio.run_coroutine_threadsafe(
                        self.controller.reply_request(message_id, reply),
                        self.loop,
                    )

                    def done(_fut) -> None:
                        if self.icon:
                            self.icon.update_menu()

                    fut.add_done_callback(done)

                return act

            yield MenuItem(header, None, enabled=False)
            yield MenuItem(
                "承諾する",
                make_reply(req.message_id, "accept"),
            )
            yield MenuItem(
                "ごめんなさい",
                make_reply(req.message_id, "decline"),
            )

    def _build_menu(self) -> Menu:
        return Menu(
            MenuItem(lambda item: self._status_text(), None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("ロビーページを開く", lambda: self._open_lobby()),
            MenuItem("投稿設定...", lambda: self._open_settings()),
            MenuItem("戦績を見る...", lambda: self._open_stats()),
            MenuItem("戦績をサーバーと同期", lambda: self._sync_stats()),
            MenuItem("募集タイプ切替", Menu(lambda: self._post_type_menu_items())),
            MenuItem("コメント切替", Menu(lambda: self._comment_menu_items())),
            MenuItem("配信URL切替", Menu(lambda: self._stream_menu_items())),
            MenuItem(
                lambda item: self._pause_menu_label(),
                Menu(lambda: self._pause_menu_items()),
            ),
            MenuItem(
                "ホスト時に IP:Port をコピー",
                lambda: self._toggle_copy_addr(),
                checked=lambda item: self.controller.copy_addr_enabled(),
            ),
            MenuItem(
                "リクエストに返信",
                Menu(lambda: self._request_menu_items()),
                visible=lambda item: (
                    self.controller._prune_pending_requests(),
                    bool(self.controller.pending_requests),
                )[-1],
            ),
            MenuItem(lambda item: self._discord_label(), lambda: self._discord_action()),
            Menu.SEPARATOR,
            MenuItem(lambda item: self._tool_label("autopunch"),
                     lambda: self._handle_tool("autopunch", "Select autopunch exe")),
            MenuItem(lambda item: self._tool_label("giuroll"),
                     lambda: self._handle_tool("giuroll", "Select giuroll exe")),
            MenuItem(lambda item: self._tool_label("soku"),
                     lambda: self._handle_tool("soku", "Select th123.exe")),
            Menu.SEPARATOR,
            MenuItem(
                lambda item: f"更新 {self.controller.update_available[0]} をダウンロード"
                if self.controller.update_available
                else "",
                lambda: self._open_update_page(),
                visible=lambda item: self.controller.update_available is not None,
            ),
            MenuItem("ログを開く", lambda: self._open_log()),
            MenuItem("ツールのパスをリセット", lambda: self._reset_paths()),
            MenuItem("終了", lambda: self._quit()),
        )

    # -----------------
    # startup
    # -----------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)

        async def startup() -> None:
            await self.controller.sync_initial()
            asyncio.ensure_future(self.controller.update_check_loop())
            asyncio.ensure_future(self.controller.stats_sync_loop())
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

        self.icon = TrayIcon(
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
    import ctypes
    import sys
    from tkinter import Tk
    from tkinter import messagebox

    ERROR_ALREADY_EXISTS = 183
    # Local\ (セッション毎) なら一般ユーザー権限で確実に作成できる
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\asobby_client_mutex")
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        root = Tk()
        root.withdraw()
        messagebox.showwarning("asobby", "asobby は既に起動しています")
        sys.exit(0)

    TrayApp().run()
