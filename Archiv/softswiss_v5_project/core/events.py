from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List


@dataclass
class StateNotifier:
    """Simple in-process push notifier for GUI refreshes.

    This replaces timer-based repaint loops for the local desktop app.
    """

    subscribers: List[Callable[[], None]] = field(default_factory=list)

    def subscribe(self, callback: Callable[[], None]) -> None:
        if callback not in self.subscribers:
            self.subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        if callback in self.subscribers:
            self.subscribers.remove(callback)

    def notify(self) -> None:
        for callback in list(self.subscribers):
            try:
                callback()
            except Exception:
                # Keep the notifier resilient; GUI callbacks should not break the engine.
                pass
