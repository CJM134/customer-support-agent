from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from app.core.config import get_settings
from app.models.schemas import FeedbackMetrics, FeedbackRecord, FeedbackRequest

logger = logging.getLogger(__name__)


class FeedbackRepository:
    def __init__(self) -> None:
        self.db_path = get_settings().feedback_db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info("反馈仓库就绪: %s", self.db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    original_reply TEXT NOT NULL,
                    revised_reply TEXT NOT NULL,
                    category TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    editor TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save(self, request: FeedbackRequest) -> FeedbackRecord:
        logger.info("保存反馈: ticket=%s accepted=%s editor=%s", request.ticket_id, request.accepted, request.editor)
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO feedback (
                    ticket_id, original_reply, revised_reply, category,
                    accepted, editor, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.ticket_id,
                    request.original_reply,
                    request.revised_reply,
                    request.category,
                    int(request.accepted),
                    request.editor,
                    request.notes,
                    created_at,
                ),
            )
            record_id = cursor.lastrowid

        return FeedbackRecord(id=record_id, created_at=created_at, **request.model_dump())

    def list_recent(self, limit: int = 20) -> list[FeedbackRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM feedback ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def metrics(self) -> FeedbackMetrics:
        logger.debug("计算反馈指标")
        rows = self.list_recent(limit=1000)
        total = len(rows)
        if total == 0:
            return FeedbackMetrics(
                total_feedback=0,
                acceptance_rate=0,
                avg_revision_ratio=0,
                by_category={},
            )

        accepted = sum(record.accepted for record in rows)
        ratios = [
            abs(len(record.revised_reply) - len(record.original_reply)) / max(len(record.original_reply), 1)
            for record in rows
        ]
        by_category = Counter(record.category for record in rows)
        return FeedbackMetrics(
            total_feedback=total,
            acceptance_rate=round(accepted / total, 3),
            avg_revision_ratio=round(sum(ratios) / total, 3),
            by_category=dict(by_category),
        )

    def _row_to_record(self, row: sqlite3.Row) -> FeedbackRecord:
        return FeedbackRecord(
            id=row["id"],
            ticket_id=row["ticket_id"],
            original_reply=row["original_reply"],
            revised_reply=row["revised_reply"],
            category=row["category"],
            accepted=bool(row["accepted"]),
            editor=row["editor"],
            notes=row["notes"],
            created_at=row["created_at"],
        )


feedback_repository = FeedbackRepository()
