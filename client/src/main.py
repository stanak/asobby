from __future__ import annotations

import asyncio
import os
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from time import sleep

from PIL import Image
from pystray import Menu, MenuItem

from controller import Controller, PAUSE_UNTIL_RESUME
from icon_art import render_icon
from tray_icon import TrayIcon
from i18n import (
    SUPPORTED_LANGS,
    get_lang,
    post_type_options,
    set_lang,
    t,
)
from services import (
    Post,
    edit_post_settings,
    edit_session_score_notify_settings,
    __version__,
)
from stats_window import open_stats_window
import toast

LOG_PATH = Path("asobby.log")
LOG_MAX_BYTES = 256 * 1024

# トレイアイコンの状態色 (幾何学マークのアクセント)
COLOR_IDLE = (128, 128, 128)
COLOR_RECRUIT = (46, 160, 67)
COLOR_BATTLE = (219, 109, 40)


def make_icon_image(
    color: tuple[int, int, int], *, badge: bool = False
) -> Image.Image:
    return render_icon(64, accent=color, frame=color, badge=badge)


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

        self._icon_cache: dict[tuple[str, bool], Image.Image] = {}
        self._pause_tick_after_id: str | None = None

    # -----------------
    # Controller sinks
    # -----------------
    def emit_log(self, level: str, text: str) -> None:
        self._append_log(level, text)

    def emit_notify(self, text: str) -> None:
        def show() -> None:
            self._notify(text)
            if self.icon:
                self.icon.update_menu()

        if self.tk_root is not None:
            self.tk_root.after(0, show)
        else:
            show()

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
            text + "\n" + t("toast.request_hint"),
            callback,
            log=lambda m: self._append_log("warn", m),
        )
        if not shown:
            self._notify(text + t("toast.request_fallback"))
        if self.icon:
            self.icon.update_menu()

    def emit_my_post(self, post: Post) -> None:
        self.post = post
        self._refresh_icon()

    def emit_lobby_activity(self) -> None:
        self._on_tk(self._refresh_icon)

    def emit_pause_state_changed(self) -> None:
        self._on_tk(self._schedule_pause_ui_tick)

    def emit_detect_ui_changed(self) -> None:
        self._on_tk(self._refresh_icon)

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
    def _icon_for(self, key: str, *, badge: bool) -> Image.Image:
        cache_key = (key, badge)
        if cache_key not in self._icon_cache:
            colors = {
                "idle": COLOR_IDLE,
                "recruit": COLOR_RECRUIT,
                "battle": COLOR_BATTLE,
            }
            self._icon_cache[cache_key] = make_icon_image(
                colors[key], badge=badge
            )
        return self._icon_cache[cache_key]

    def _status_text(self) -> str:
        return self.controller.tray_status_text()

    def _refresh_icon(self) -> None:
        if not self.icon:
            return
        key = self.controller.tray_icon_key()
        badge = self.controller.lobby_has_other_posts()
        self.icon.icon = self._icon_for(key, badge=badge)
        self.icon.title = f"asobby v{__version__} - {self._status_text()}"
        self.icon.update_menu()

    def _cancel_pause_ui_tick(self) -> None:
        if self.tk_root is None or self._pause_tick_after_id is None:
            return
        try:
            self.tk_root.after_cancel(self._pause_tick_after_id)
        except ValueError:
            pass
        self._pause_tick_after_id = None

    def _schedule_pause_ui_tick(self) -> None:
        """一時停止中はトレイ表示を定期的に更新して残り時間をカウントダウンする。"""
        self._cancel_pause_ui_tick()
        if not self.controller.is_detect_paused():
            self._refresh_icon()
            return
        self._refresh_icon()
        if self.tk_root is not None:
            self._pause_tick_after_id = self.tk_root.after(
                30_000,
                self._schedule_pause_ui_tick,
            )

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
        current = {
            **self.controller.config_mgr.get_post_defaults(),
            "ping_warn_enabled": self.controller.ping_warn_enabled(),
            "ping_warn_ms": self.controller.ping_warn_ms(),
            "ping_warn_giuroll_ms": self.controller.ping_warn_giuroll_ms(),
        }

        def apply(result: dict) -> None:
            for key, value in result.items():
                if key in ("ping_warn_enabled", "ping_warn_ms", "ping_warn_giuroll_ms"):
                    continue
                self.controller.config_mgr.set_post_default(key, value)
            self.controller.set_ping_warn_enabled(bool(result.get("ping_warn_enabled", True)))
            self.controller.set_ping_warn_ms(int(result.get("ping_warn_ms", 60)))
            self.controller.set_ping_warn_giuroll_ms(
                int(result.get("ping_warn_giuroll_ms", 100))
            )
            self.controller.update_my_post(
                post_type=result["post_type"],
                comment=result["comment"],
                stream_url=result["stream_url"],
                challenge_upper=result.get("challenge_upper", False),
            )
            if self.controller.has_active_post():
                asyncio.run_coroutine_threadsafe(
                    self.controller.enqueue_settings_update(),
                    self.loop,
                )
            if self.icon:
                self.icon.update_menu()

        self._on_tk(lambda: edit_post_settings(self.tk_root, current, apply))

    def _open_session_score_settings(self) -> None:
        current = {
            "session_score_notify_enabled": self.controller.session_score_notify_enabled(),
            "session_score_notify_mode": self.controller.session_score_notify_mode(),
            "session_score_notify_rules": self.controller.session_score_notify_rules(),
        }

        def apply(result: dict) -> None:
            self.controller.set_session_score_notify_enabled(
                bool(result.get("session_score_notify_enabled", False))
            )
            self.controller.set_session_score_notify_mode(
                str(result.get("session_score_notify_mode", "all"))
            )
            self.controller.set_session_score_notify_rules(
                result.get("session_score_notify_rules") or []
            )
            if self.icon:
                self.icon.update_menu()

        self._on_tk(
            lambda: edit_session_score_notify_settings(
                self.tk_root, current, apply
            )
        )

    def _toggle_session_score_notify(self) -> None:
        self.controller.set_session_score_notify_enabled(
            not self.controller.session_score_notify_enabled()
        )
        if self.icon:
            self.icon.update_menu()

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
            return t("tray.discord_logout", name=name)
        return t("tray.discord_login")

    def _discord_action(self) -> None:
        if self.controller.is_logged_in():
            def done(_fut) -> None:
                if self.icon:
                    self.icon.update_menu()

            fut = asyncio.run_coroutine_threadsafe(
                self.controller.logout_discord(), self.loop
            )
            fut.add_done_callback(done)
            return

        def open_browser(url: str) -> None:
            webbrowser.open(url)

        def done(_fut) -> None:
            if self.icon:
                self.icon.update_menu()

        fut = asyncio.run_coroutine_threadsafe(
            self.controller.login_discord(open_browser, force=True), self.loop
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

        for label, value in post_type_options():
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
            t("tray.none"),
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
            yield MenuItem(t("tray.add_comment_hint"), None, enabled=False)

    def _stream_menu_items(self):
        """配信URL切替サブメニュー (プリセットからラジオ選択)。"""
        current = self.controller.my_post.stream_url or ""
        presets = self.controller.stream_presets()

        def make_action(text: str):
            def act(icon, item):
                self.controller.set_active_stream(text)
            return act

        yield MenuItem(
            t("tray.none"),
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
            yield MenuItem(t("tray.add_stream_hint"), None, enabled=False)

    def _pause_menu_items(self):
        """ホスト自動投稿の一時停止サブメニュー。"""
        def make_pause(seconds: float):
            def act(icon, item):
                self.controller.pause_auto_detect(seconds)
            return act

        def resume(icon, item):
            self.controller.resume_auto_detect()

        paused = self.controller.is_detect_paused()
        if paused:
            yield MenuItem(
                lambda item: t(
                    "tray.pause_running",
                    remaining=self.controller.detect_pause_remaining_label(),
                ),
                None,
                enabled=False,
            )
            yield MenuItem(t("tray.pause_resume"), resume)
            yield Menu.SEPARATOR
        yield MenuItem(t("tray.pause_30m"), make_pause(30 * 60))
        yield MenuItem(t("tray.pause_1h"), make_pause(60 * 60))
        yield MenuItem(t("tray.pause_3h"), make_pause(3 * 60 * 60))
        yield MenuItem(t("tray.pause_until_resume"), make_pause(PAUSE_UNTIL_RESUME))

    def _pause_menu_label(self) -> str:
        if self.controller.is_detect_paused():
            remaining = self.controller.detect_pause_remaining_label()
            return t("tray.pause_active", remaining=remaining)
        return t("tray.pause")

    def _toggle_copy_addr(self) -> None:
        self.controller.set_copy_addr_enabled(
            not self.controller.copy_addr_enabled()
        )
        if self.icon:
            self.icon.update_menu()

    def _toggle_challenge_upper(self) -> None:
        self.controller.set_challenge_upper_enabled(
            not self.controller.challenge_upper_enabled()
        )
        if self.icon:
            self.icon.update_menu()

    def _toggle_ping_warn(self) -> None:
        self.controller.set_ping_warn_enabled(
            not self.controller.ping_warn_enabled()
        )
        if self.controller.has_active_post():
            asyncio.run_coroutine_threadsafe(
                self.controller.enqueue_settings_update(),
                self.loop,
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
                t("tray.accept"),
                make_reply(req.message_id, "accept"),
            )
            yield MenuItem(
                t("tray.decline"),
                make_reply(req.message_id, "decline"),
            )

    def _lang_menu_items(self):
        """言語切替サブメニュー (ja/en のラジオ選択)。"""
        def make_action(lang: str):
            def act(icon, item):
                self._set_lang(lang)
            return act

        for lang in SUPPORTED_LANGS:
            yield MenuItem(
                t(f"lang.{lang}"),
                make_action(lang),
                radio=True,
                checked=lambda item, lang=lang: get_lang() == lang,
            )

    def _set_lang(self, lang: str) -> None:
        set_lang(lang)
        if self.icon:
            self.icon.menu = self._build_menu()
            self.icon.update_menu()
            self._refresh_icon()

    def _build_menu(self) -> Menu:
        return Menu(
            MenuItem(lambda item: self._status_text(), None, enabled=False),
            MenuItem(lambda item: t("tray.version", version=__version__), None, enabled=False),
            Menu.SEPARATOR,
            MenuItem(t("tray.open_lobby"), lambda: self._open_lobby()),
            MenuItem(t("tray.settings"), lambda: self._open_settings()),
            MenuItem(t("tray.stats"), lambda: self._open_stats()),
            MenuItem(t("tray.sync_stats"), lambda: self._sync_stats()),
            MenuItem(t("tray.post_type"), Menu(lambda: self._post_type_menu_items())),
            MenuItem(t("tray.comment"), Menu(lambda: self._comment_menu_items())),
            MenuItem(t("tray.stream"), Menu(lambda: self._stream_menu_items())),
            MenuItem(
                lambda item: self._pause_menu_label(),
                Menu(lambda: self._pause_menu_items()),
            ),
            MenuItem(
                t("tray.copy_addr"),
                lambda: self._toggle_copy_addr(),
                checked=lambda item: self.controller.copy_addr_enabled(),
            ),
            MenuItem(
                t("tray.challenge_upper"),
                lambda: self._toggle_challenge_upper(),
                checked=lambda item: self.controller.challenge_upper_enabled(),
                visible=lambda item: self.controller.my_post.post_type == "ranked",
            ),
            MenuItem(
                t("tray.ping_warn"),
                lambda: self._toggle_ping_warn(),
                checked=lambda item: self.controller.ping_warn_enabled(),
            ),
            MenuItem(
                t("tray.session_score_notify"),
                lambda: self._toggle_session_score_notify(),
                checked=lambda item: self.controller.session_score_notify_enabled(),
            ),
            MenuItem(
                t("tray.session_score_settings"),
                lambda: self._open_session_score_settings(),
            ),
            MenuItem(
                t("tray.reply_requests"),
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
                lambda item: t(
                    "tray.download_update",
                    tag=self.controller.update_available[0],
                )
                if self.controller.update_available
                else "",
                lambda: self._open_update_page(),
                visible=lambda item: self.controller.update_available is not None,
            ),
            MenuItem(t("tray.open_log"), lambda: self._open_log()),
            MenuItem(t("tray.reset_paths"), lambda: self._reset_paths()),
            MenuItem(t("lang.menu"), Menu(lambda: self._lang_menu_items())),
            MenuItem(t("tray.quit"), lambda: self._quit()),
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
            asyncio.ensure_future(self.controller.lobby_poll_loop())
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
            icon=self._icon_for("idle", badge=False),
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
        messagebox.showwarning("asobby", t("tray.already_running"))
        sys.exit(0)

    TrayApp().run()
