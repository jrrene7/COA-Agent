from dataclasses import dataclass
from typing import List


@dataclass
class Snapshot:
    step_id: str
    state_data: dict


class Run:
    """Holds final graph state and ordered per-node updates."""

    def __init__(self, final_state: dict, snapshots: List[Snapshot]):
        self._final_state = final_state
        self.snapshots = snapshots

    def get_final_state(self) -> dict:
        return dict(self._final_state)
