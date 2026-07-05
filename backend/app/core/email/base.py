"""The ``EmailSender`` interface and message value object (docs/PF §9)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(ABC):
    """Swappable email transport. Consumers depend on this, not on an impl."""

    @abstractmethod
    def send(self, message: EmailMessage) -> None:
        """Deliver ``message``. Implementations must not raise on transient
        provider issues in a way that breaks the calling business flow — log
        and (later) enqueue instead. The test sender always succeeds.
        """
        raise NotImplementedError
