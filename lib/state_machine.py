from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar, Generic, List, Callable, Dict, Optional, Any

T = TypeVar("T")


@dataclass
class Snapshot:
    step_id: str
    state_data: dict


class Run:
    """Holds the result of a completed StateMachine execution."""

    def __init__(self, final_state: dict, snapshots: List[Snapshot]):
        self._final_state = final_state
        self.snapshots = snapshots

    def get_final_state(self) -> dict:
        return dict(self._final_state)


class EntryPoint(Generic[T]):
    step_id = "__entry__"


class Termination(Generic[T]):
    step_id = "__termination__"


class Step(Generic[T]):
    def __init__(self, step_id: str, fn: Callable[[T], dict]):
        self.step_id = step_id
        self.fn = fn

    def __call__(self, state: T) -> dict:
        return self.fn(state)


class StateMachineBuildError(Exception):
    pass


class StateMachine(Generic[T]):
    """Sequential state machine that merges step return values into shared state."""

    def __init__(self, state_type: type):
        self.state_type = state_type
        self._steps: list = []
        self._connections: Dict[str, Any] = {}

    def add_steps(self, steps: list) -> None:
        self._steps = list(steps)

    def connect(self, from_step: Any, to_step: Any) -> None:
        self._connections[from_step.step_id] = to_step

    def run(self, initial_state: T) -> Run:
        state: dict = dict(initial_state)
        snapshots: List[Snapshot] = []

        current = self._connections.get(EntryPoint.step_id)
        if current is None:
            raise StateMachineBuildError("No step connected from EntryPoint.")

        while current is not None and not isinstance(current, Termination):
            step_id = current.step_id
            updates = current(state)
            if not isinstance(updates, dict):
                raise StateMachineBuildError(
                    f"Step '{step_id}' must return a dict, got {type(updates).__name__}."
                )
            state.update(updates)
            snapshots.append(Snapshot(step_id=step_id, state_data=dict(updates)))
            current = self._connections.get(step_id)

        return Run(final_state=state, snapshots=snapshots)
