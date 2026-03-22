from __future__ import annotations

from typing import Iterable
from time import sleep

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Grid, Container
from textual.widgets import Select, Label, Input, Button, RichLog
from textual.reactive import reactive
from textual.message import Message

from controller import Controller
from services import MODE_OPTIONS, Post, pick_path
from widgets.lobby_table import LobbyTable, CopyRequested


class LogMessage(Message):
    def __init__(self, level: str, text: str):
        super().__init__()
        self.level = level
        self.text = text


class SokulobbyApp(App):
    theme = "solarized-dark"
    CSS = """
    Screen {
    }

    #root {
        layout: horizontal;
        height: 100%;
    }

    LobbyTable {
        layout: vertical;
        height: 1fr;
        overflow: hidden;
        width: 1fr;
    }

    #post-rank {
        width: 16;
    }

    #bottom-pane {
        layout: horizontal;
        height: 18;
        border-top: solid $panel;
        overflow: hidden hidden;
    }

    #post-form {
        layout: vertical;
        width: 2fr;
        padding-left: 1;
        padding-right: 1;
    }

    #post-form Input,
    #post-form Select {
        width: 100%;
    }

    .form-label {
        content-align: right middle;
        color: $text-muted;
    }

    #action-pane {
        width: 24;
        padding-left: 1;
        padding-right: 1;
        border-left: solid $panel;
        layout: vertical;
        overflow: hidden hidden;
    }

    #post-grid {
        layout: grid;
        grid-size: 2 3;
        grid-columns: 12 1fr;
        grid-rows: auto auto auto;
        grid-gutter: 1 2;
        width: 100%;
        height: auto;
    }

    #action-pane Button {
        width: 100%;
        margin-bottom: 1;
    }

    #action-pane Button:last-child {
        margin-bottom: 0;
    }

    #log {
        height: 4;
    }
    """
    my_post = reactive(Post)
    tool_labels = reactive({
        "soku": "set soku path",
        "giuroll": "set giuroll path",
        "autopunch": "set autopunch path",
    })

    def __init__(self) -> None:
        super().__init__()
        self.controller = Controller(self)
        self.lobby = LobbyTable(self.emit_log)
        self._tool_buttons_ready = False

    def compose(self) -> ComposeResult:
        yield self.lobby
        with Horizontal(id="bottom-pane"):
            with Container(id="post-form"):
                with Grid(id="post-grid"):
                    yield Label("Post Rank :", classes="form-label")
                    yield Select(
                        MODE_OPTIONS[1:],
                        value="any",
                        id="post-rank",
                    )

                    yield Label("Comment   :", classes="form-label")
                    yield Input(placeholder="Comment", id="post-comment")

                    yield Label("Stream URL:", classes="form-label")
                    yield Input(placeholder="https://...", id="stream-url")

                yield Label("Log:", classes="form-label")
                yield RichLog(id="log", wrap=True, markup=True, max_lines=100)

            with Vertical(id="action-pane"):
                yield Button(self.tool_labels["autopunch"], id="btn-autopunch")
                yield Button(self.tool_labels["giuroll"], id="btn-giuroll")
                yield Button(self.tool_labels["soku"], id="btn-soku")
                yield Button("Reset Paths", id="btn-reset")

    async def on_mount(self) -> None:
        self._tool_buttons_ready = True
        await self.controller.sync_initial()
        self.run_worker(self.controller.sse_loop(), name="sse", thread=False)
        self.run_worker(self.controller.detector_loop(), name="detector", thread=False)
        self.run_worker(self.controller.api_loop(), name="api", thread=False)
        self.lobby.table.focus()

        # 初期値の配置
        post = self.controller.my_post
        self.query_one("#post-rank", Select).value = post.rank or "any"
        self.query_one("#post-comment", Input).value = post.comment or ""
        self.query_one("#stream-url", Input).value = post.stream_url or ""
        self._refresh_tool_buttons()

    async def on_unmount(self) -> None:
        await self.controller.close()

    async def on_copy_requested(self, msg: CopyRequested) -> None:
        self.copy_to_clipboard(msg.text)

    async def on_log_message(self, msg: LogMessage) -> None:
        log = self.query_one("#log", RichLog)
        if msg.level == "error":
            log.write(f"[red]{msg.text}[/red]")
        elif msg.level == "warn":
            log.write(f"[yellow]{msg.text}[/yellow]")
        else:
            log.write(msg.text)

    def emit_log(self, level: str, text: str) -> None:
        self.post_message(LogMessage(level, text))

    def emit_posts(self, posts: Iterable[Post]) -> None:
        self.lobby.set_posts(posts)

    def emit_my_post(self, post: Post) -> None:
        self.my_post = post

    def emit_btn_labels(self, d: dict) -> None:
        self.tool_labels = d

    def watch_post(self, post: Post) -> None:
        rank = self.query_one("#post-rank", Select)
        comment = self.query_one("#post-comment", Input)
        stream = self.query_one("#stream-url", Input)
        if rank.value != post.rank:
            rank.value = post.rank
        if comment.value != post.comment:
            comment.value = post.comment
        if stream.value != post.stream_url:
            stream.value = post.stream_url

    def watch_tool_labels(self, value) -> None:
        if not self._tool_buttons_ready:
            return
        self._refresh_tool_buttons()

    def _refresh_tool_buttons(self) -> None:
        self.query_one("#btn-giuroll", Button).label = self.controller.tool_mgr.button_label("giuroll")
        self.query_one("#btn-autopunch", Button).label = self.controller.tool_mgr.button_label("autopunch")
        self.query_one("#btn-soku", Button).label = self.controller.tool_mgr.button_label("soku")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "post-rank":
            self.controller.update_my_post(rank=str(event.value))
            self.controller.config_mgr.set_post_default("rank", str(event.value))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "post-comment":
            self.controller.update_my_post(comment=str(event.value))
            self.controller.config_mgr.set_post_default("comment", str(event.value))
        elif event.input.id == "stream-url":
            self.controller.update_my_post(stream_url=str(event.value))
            self.controller.config_mgr.set_post_default("stream_url", str(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id

        if bid == "btn-autopunch":
            self._handle_tool_button("autopunch", "Select autopunch exe")
        elif bid == "btn-giuroll":
            self._handle_tool_button("giuroll", "Select giuroll exe")
        elif bid == "btn-soku":
            self._handle_tool_button("soku", "Select th123.exe")
        elif bid == "btn-reset":
            self.controller.tool_mgr.clear_path("autopunch")
            self.controller.tool_mgr.clear_path("giuroll")
            self.controller.tool_mgr.clear_path("soku")
            self.controller.tool_mgr.reset_state()
        self._refresh_tool_buttons()

    def _handle_tool_button(self, tool_name: str, title: str) -> None:
        entry = self.controller.tool_mgr.get(tool_name)
        if entry.state.name == "NO_PATH" and not entry.is_active:
            path = pick_path(title)
            if path:
                self.controller.tool_mgr.set_path(tool_name, path)
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
            elif entry.state.name == "LOADED":
                pass
        self._refresh_tool_buttons()


if __name__ == "__main__":
    SokulobbyApp().run()
