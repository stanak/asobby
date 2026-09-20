"""Keep selection evidence separate from resolved fighters and result identity."""
from detect_api import DetectionState

UNKNOWN = (None, None)


class RandomSelectionTracker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._connection = None
        self._mode = ""
        self._scene = None
        self._ready = UNKNOWN
        self._candidate = UNKNOWN
        self._locked = False
        self.battle_choices = UNKNOWN

    def observe(self, st: DetectionState) -> None:
        if not st.alive or st.net_side not in ("host", "client") or st.mode not in ("charsel", "loading", "battle"):
            self.reset()
            return
        connection = (st.process_id, st.exe_path, st.net_side, st.lprof, st.rprof)
        if connection != self._connection:
            self.reset()
            self._connection = connection
        if st.mode == "charsel":
            selection = st.character_selection
            scene = selection.scene_address if selection else None
            if self._mode != "charsel" or scene != self._scene:
                self._ready, self._locked = UNKNOWN, False
                self._candidate = UNKNOWN
            self._scene = scene
            if selection is None or (selection.left_stage, selection.right_stage) != (3, 3):
                # Cancellation (including Random -> another character) revokes
                # evidence. Merely hovering Random never sets a choice.
                self._ready, self._locked = UNKNOWN, False
                self._candidate = (selection.left_char, selection.right_char) if selection else UNKNOWN
            elif not self._locked:
                # Attaching after both players were ready may see an already
                # resolved Random. Do not call that a confirmed direct choice.
                self._ready = tuple(True if char == 20 else False if char == previous else None
                                    for char, previous in zip((selection.left_char, selection.right_char), self._candidate))
                self._locked = True
            # Once both sides are ready, keep the choice through random
            # resolution. Reading just the last Select sample loses Random.
        elif st.mode == "battle" and self._mode != "battle":
            self.battle_choices = self._ready if self._mode in ("charsel", "loading") else UNKNOWN
            self._ready, self._locked = UNKNOWN, False
        self._mode = st.mode
