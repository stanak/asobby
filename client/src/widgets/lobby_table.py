from __future__ import annotations

from typing import Dict, List, Iterable
import webbrowser

from rich.text import Text
from textual.message import Message
from textual.containers import Horizontal
from textual.widgets import DataTable, Static, Label, Select

from services import Post, NET_BATTLE, MODE_OPTIONS


class CopyRequested(Message):
    def __init__(self, post: Post) -> None:
        super().__init__()
        self.text = post.addr


class LobbyTable(Static):
    DEFAULT_CSS = """
    LobbyTable > #data-table {
        height: 1fr;
    }

    LobbyTable > #filter-bar {
        layout: horizontal;
        height: auto;
        padding: 0 1;
        border-top: solid $panel;
        align: left middle;
        overflow: hidden hidden;
    }

    LobbyTable > #filter-rank {
        width: 16;
    }
    """

    def __init__(self, log_sink) -> None:
        super().__init__()
        self.table = DataTable(id="data-table")
        self._posts: List[Post] = []
        self._post_by_rowkey: Dict[str, Post] = {}
        self.filter_rank = "all"
        self.log_sink = log_sink

    def compose(self):
        yield self.table
        with Horizontal(id="filter-bar"):
            yield Label("Filter Rank:")
            yield Select(
                MODE_OPTIONS,
                value="all",
                id="filter-rank",
            )

    def on_mount(self) -> None:
        self.table.add_column("Match", width=50)
        self.table.add_column("Rank", width=5)
        self.table.add_column("Cap", width=3)
        self.table.add_column("Stream", width=6)
        self.table.add_column("Comment")
        self.table.cursor_type = "cell"
        self.table.zebra_stripes = True
        self.table.show_header = True
        self.table.show_row_labels = False

    def _cell(self, post: Post, s: str) -> Text:
        if post.net_status == NET_BATTLE:
            return Text(s, style="dim")
        return Text(s)

    def _filter_posts(self, posts: Iterable[Post]) -> List[Post]:
        posts = list(posts)
        rank = (self.filter_rank or "any").lower()
        if rank in ("all", "any", ""):
            return sorted(posts, key=lambda p: p.updated_at, reverse=True)

        out = [p for p in posts if (p.rank or "any").lower() == rank]
        return sorted(out, key=lambda p: p.updated_at, reverse=True)

    def set_posts(self, posts: Iterable[Post]) -> None:
        self._post_by_rowkey = {}
        self.table.clear()

        for p in self._filter_posts(posts):
            g = "G" if bool(getattr(p, "giuroll", False)) else ""
            a = "A" if bool(getattr(p, "autopunch", False)) else ""
            stream = "▶" if (p.stream_url or "").strip() else ""
            comment = (getattr(p, "comment", "") or "").strip()
            match_status = (getattr(p, "match_status", "") or "").strip()
            rank = (getattr(p, "rank", "Any") or "")

            row_key = self.table.add_row(
                self._cell(p, match_status),
                self._cell(p, rank),
                self._cell(p, a + g),
                self._cell(p, stream),
                self._cell(p, comment),
                key=p.id,
            )
            self._post_by_rowkey[row_key] = p

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        row_key = event.cell_key.row_key
        post = self._post_by_rowkey.get(row_key)
        if not post:
            return

        col = event.coordinate.column
        STREAM_COL_INDEX = 3

        if col == STREAM_COL_INDEX:
            url = (post.stream_url or "").strip()
            if url:
                webbrowser.open(url)
        else:
            self.post_message(CopyRequested(post))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "filter-rank":
            self.filter_rank = str(event.value)
            self.set_posts(self._posts)
