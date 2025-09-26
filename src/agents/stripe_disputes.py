"""Agent that publishes Stripe dispute exports to S3."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

from src.core.artifacts import put_bytes

_HEADERS = ["id", "amount", "currency", "status", "reason"]


class StripeDisputesAgent:
    """Serialises dispute records to CSV and uploads them as an artifact."""

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        disputes = payload.get("disputes", [])
        if not isinstance(disputes, Iterable):
            raise ValueError("payload.disputes must be an iterable of records")

        records = list(disputes) if not isinstance(disputes, list) else disputes

        key = payload.get("s3_key") or self._default_s3_key()
        mime_type = "text/csv"
        csv_bytes = self._build_csv_bytes(records)
        url = put_bytes(key, csv_bytes, mime_type)
        return {
            "artifact_url": url,
            "s3_key": key,
            "rows": len(records),
            "mime_type": mime_type,
        }

    def _default_s3_key(self) -> str:
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"stripe/disputes-{timestamp}.csv"

    def _build_csv_bytes(self, disputes: Iterable[Dict[str, Any]]) -> bytes:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in disputes:
            writer.writerow({
                "id": row.get("id", ""),
                "amount": row.get("amount", ""),
                "currency": row.get("currency", ""),
                "status": row.get("status", ""),
                "reason": row.get("reason", ""),
            })
        return buffer.getvalue().encode("utf-8")


def build_agent() -> StripeDisputesAgent:
    return StripeDisputesAgent()


__all__ = ["StripeDisputesAgent", "build_agent"]
