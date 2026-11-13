"""Retention worker — periodic cleanup of old records.

Runs in the background (asyncio task launched at startup) and deletes:

  - event_inbox rows with status 'processed' older than RETENTION_EVENT_INBOX_DAYS.
  - investigations with status 'completed' older than RETENTION_INVESTIGATION_DAYS
    AND no open feedback (decision != 'needs_more_evidence').

Retention is conservative by default:
  - Only 'processed' events are deleted (never 'failed' — those need attention).
  - Only 'completed' investigations are eligible (never 'review_required').
  - Deletes are batched (max 500 rows per run) to avoid long locks.
  - Interval: every 6 hours.

These defaults can be overridden via environment variables.
Never deletes evidence records for open/review_required investigations.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event_inbox import EventInbox
from app.models.investigation import Investigation

logger = structlog.get_logger()

_DEFAULT_EVENT_INBOX_DAYS = 30
_DEFAULT_INVESTIGATION_DAYS = 90
_DEFAULT_INTERVAL_SECONDS = 6 * 3600   # 6 hours
_BATCH_SIZE = 500


class RetentionWorker:
    """Periodic cleanup of old processed events and completed investigations."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        event_inbox_days: int = _DEFAULT_EVENT_INBOX_DAYS,
        investigation_days: int = _DEFAULT_INVESTIGATION_DAYS,
        interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._event_inbox_days = event_inbox_days
        self._investigation_days = investigation_days
        self._interval_seconds = interval_seconds
        self._running = False

    async def run(self) -> None:
        """Run cleanup loop until cancelled."""
        self._running = True
        logger.info(
            "retention.worker.started",
            event_inbox_days=self._event_inbox_days,
            investigation_days=self._investigation_days,
            interval_seconds=self._interval_seconds,
        )
        while self._running:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("retention.worker.error", error=str(exc))

            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                break

        logger.info("retention.worker.stopped")

    async def _run_once(self) -> None:
        """Execute one cleanup pass."""
        now = datetime.now(tz=UTC)

        async with self._session_factory() as session:
            deleted_events = await self._purge_events(session, now)
            await session.commit()

        async with self._session_factory() as session:
            deleted_investigations = await self._purge_investigations(session, now)
            await session.commit()

        if deleted_events or deleted_investigations:
            logger.info(
                "retention.worker.purged",
                events=deleted_events,
                investigations=deleted_investigations,
            )

    async def _purge_events(self, session: AsyncSession, now: datetime) -> int:
        """Delete old processed event_inbox rows. Returns count deleted."""
        cutoff = now - timedelta(days=self._event_inbox_days)

        # Find IDs to delete first (avoid locking large scans)
        result = await session.execute(
            select(EventInbox.id)
            .where(
                EventInbox.processing_status == "processed",
                EventInbox.processed_at < cutoff,
            )
            .limit(_BATCH_SIZE)
        )
        ids = [row[0] for row in result.fetchall()]
        if not ids:
            return 0

        await session.execute(
            delete(EventInbox).where(EventInbox.id.in_(ids))
        )
        return len(ids)

    async def _purge_investigations(self, session: AsyncSession, now: datetime) -> int:
        """Delete old completed investigations with no pending feedback. Returns count deleted."""
        cutoff = now - timedelta(days=self._investigation_days)

        result = await session.execute(
            select(Investigation.id)
            .where(
                Investigation.status == "completed",
                Investigation.completed_at < cutoff,
            )
            .limit(_BATCH_SIZE)
        )
        candidate_ids = [row[0] for row in result.fetchall()]
        if not candidate_ids:
            return 0

        from app.models.investigation_feedback import InvestigationFeedback

        # Exclude investigations that have 'needs_more_evidence' feedback
        pending_result = await session.execute(
            select(InvestigationFeedback.investigation_id)
            .where(
                InvestigationFeedback.investigation_id.in_(candidate_ids),
                InvestigationFeedback.decision == "needs_more_evidence",
            )
        )
        exclude_ids = {row[0] for row in pending_result.fetchall()}
        safe_to_delete = [i for i in candidate_ids if i not in exclude_ids]

        if not safe_to_delete:
            return 0

        await session.execute(
            delete(Investigation).where(Investigation.id.in_(safe_to_delete))
        )
        return len(safe_to_delete)
