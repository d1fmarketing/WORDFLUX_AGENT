#!/usr/bin/env python3
"""Content Approver Agent - Handles content approval workflow."""

import logging
import os
import json
from datetime import datetime, timezone
from typing import Dict, Any

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ContentApproverAgent(BaseAgent):
    """Agent that handles content approval and moves it to the next stage."""

    @property
    def name(self) -> str:
        return "content_approver"

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Approve content and prepare it for scheduling/publishing.

        Expected payload:
        {
            "action": "approve",
            "card": {
                "id": "c-12345",
                "title": "Blog post about Redis",
                "meta": {
                    "author": "john",
                    "reviewed_by": "alice",
                    "review_notes": "Looks good, minor typos fixed"
                }
            },
            "from_column": "Waiting Approval",
            "cockpit": true
        }
        """
        card = payload.get("card", {})
        card_id = card.get("id", "unknown")
        title = card.get("title", "Untitled")
        meta = card.get("meta", {})
        from_column = payload.get("from_column", "Unknown")

        logger.info(f"Approving content: {title} (ID: {card_id})")

        try:
            approval_timestamp = datetime.now(timezone.utc)
            approval_id = f"approval-{card_id}-{int(approval_timestamp.timestamp())}"

            # Get approver info (from environment or payload)
            approver = meta.get("reviewed_by", os.getenv("DEFAULT_APPROVER", "system"))
            review_notes = meta.get("review_notes", "")

            # Build approval record
            approval_data = {
                "approval_id": approval_id,
                "card_id": card_id,
                "title": title,
                "approved_by": approver,
                "approved_at": approval_timestamp.isoformat(),
                "from_status": from_column,
                "to_status": "Scheduled",
                "review_notes": review_notes,
                "metadata": {
                    "author": meta.get("author", "unknown"),
                    "content_type": meta.get("content_type", "general"),
                    "target_platform": meta.get("platform", "default")
                }
            }

            # Store approval in Redis for audit trail
            if os.getenv("REDIS_URL"):
                self._store_approval(approval_data)

            # Calculate scheduled time if not provided
            scheduled_time = meta.get("scheduled_time")
            if not scheduled_time:
                # Default: schedule for next business day at 10 AM
                from datetime import timedelta
                tomorrow = approval_timestamp + timedelta(days=1)
                scheduled_time = tomorrow.replace(hour=10, minute=0, second=0).isoformat()

            approval_data["scheduled_time"] = scheduled_time

            # Send notifications
            self._send_approval_notifications(card, approval_data)

            # Update external systems
            self._update_external_systems(card, approval_data)

            return {
                "success": True,
                "card_id": card_id,
                "approval_id": approval_id,
                "approved_by": approver,
                "scheduled_time": scheduled_time,
                "message": f"Content approved: {title}"
            }

        except Exception as e:
            logger.error(f"Failed to approve content {card_id}: {e}")
            return {
                "success": False,
                "card_id": card_id,
                "error": str(e),
                "message": f"Failed to approve: {title}"
            }

    def _store_approval(self, approval_data: Dict[str, Any]) -> None:
        """Store approval record in Redis for audit trail."""
        try:
            import redis
            r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

            # Store approval record
            r.setex(
                f"approval:{approval_data['approval_id']}",
                2592000,  # 30 days TTL
                json.dumps(approval_data)
            )

            # Add to approvals list for the card
            r.lpush(f"approvals:card:{approval_data['card_id']}", approval_data['approval_id'])

            # Update daily approval metrics
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            r.hincrby(f"metrics:approvals:{today}", approval_data['approved_by'], 1)
            r.expire(f"metrics:approvals:{today}", 604800)  # 7 days TTL

            logger.info(f"Stored approval record: {approval_data['approval_id']}")

        except Exception as e:
            logger.warning(f"Could not store approval in Redis: {e}")

    def _send_approval_notifications(self, card: Dict[str, Any], approval_data: Dict[str, Any]) -> None:
        """Send notifications about content approval."""
        try:
            if not os.getenv("SLACK_WEBHOOK_URL"):
                return

            from src.agents.slack_notifier import SlackNotifierAgent
            slack = SlackNotifierAgent()

            # Notify author and team
            message = f"✅ Content Approved: {card.get('title', 'Untitled')}"
            author = approval_data["metadata"].get("author")
            if author and author != "unknown":
                message += f"\nAuthor: @{author}"
            message += f"\nApproved by: {approval_data['approved_by']}"
            message += f"\nScheduled for: {approval_data['scheduled_time'][:16]}"

            if approval_data.get("review_notes"):
                message += f"\nNotes: {approval_data['review_notes']}"

            slack.run({
                "message": message,
                "channel": "#content-approved",
                "username": "Approval Bot",
                "icon_emoji": ":white_check_mark:",
                "export_type": "Content Approval",
                "job_id": approval_data["approval_id"]
            })

            logger.info("Sent approval notification via Slack")

        except Exception as e:
            logger.warning(f"Could not send approval notification: {e}")

    def _update_external_systems(self, card: Dict[str, Any], approval_data: Dict[str, Any]) -> None:
        """Update external project management systems."""
        try:
            # Update Linear if configured
            if os.getenv("LINEAR_API_KEY"):
                from src.agents.linear_connector import LinearConnectorAgent
                linear = LinearConnectorAgent()
                linear.run({
                    "action": "update_status",
                    "issue_id": card.get("linear_id", card.get("id")),
                    "status": "Approved",
                    "comment": f"Approved by {approval_data['approved_by']}. Scheduled for {approval_data['scheduled_time'][:10]}",
                    "labels": ["approved", "scheduled"]
                })
                logger.info("Updated Linear with approval status")

            # Update content calendar if integrated
            if os.getenv("CALENDAR_WEBHOOK"):
                # This would update a content calendar system
                logger.info("Would update content calendar with scheduled item")

        except Exception as e:
            logger.debug(f"Could not update external systems: {e}")


def build_agent() -> ContentApproverAgent:
    """Factory function to create ContentApproverAgent instance."""
    return ContentApproverAgent()


__all__ = ["ContentApproverAgent", "build_agent"]