"""Per-user queue manager.

Guarantees that each user has at most one form being processed at a time.
Uses an asyncio.Semaphore(1) per user so concurrent requests are queued,
not rejected.

Also holds asyncio.Event objects used by the background processor to wait
for the user to supply personal-info answers.
"""

import asyncio
from typing import Dict


class QueueManager:
    def __init__(self) -> None:
        # One semaphore (capacity=1) per user_id
        self._semaphores: Dict[int, asyncio.Semaphore] = {}
        # One event per active submission waiting for personal info
        self._personal_info_events: Dict[int, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def _get_semaphore(self, user_id: int) -> asyncio.Semaphore:
        async with self._lock:
            if user_id not in self._semaphores:
                self._semaphores[user_id] = asyncio.Semaphore(1)
            return self._semaphores[user_id]

    async def acquire(self, user_id: int) -> None:
        """Block until the user's slot is free."""
        sem = await self._get_semaphore(user_id)
        await sem.acquire()

    def release(self, user_id: int) -> None:
        """Release the user's slot."""
        if user_id in self._semaphores:
            self._semaphores[user_id].release()

    # ── Personal-info event helpers ───────────────────────────────────────────

    def create_personal_info_event(self, submission_id: int) -> asyncio.Event:
        event = asyncio.Event()
        self._personal_info_events[submission_id] = event
        return event

    def signal_personal_info_ready(self, submission_id: int) -> None:
        """Called by the API endpoint when the user submits their personal info."""
        event = self._personal_info_events.get(submission_id)
        if event:
            event.set()

    def cleanup_event(self, submission_id: int) -> None:
        self._personal_info_events.pop(submission_id, None)


# Singleton used throughout the app
queue_manager = QueueManager()
