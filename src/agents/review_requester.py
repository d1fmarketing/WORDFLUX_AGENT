#!/usr/bin/env python3
"""Review Requester Agent - Sends content for review and notifies reviewers."""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, List

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ReviewRequesterAgent(BaseAgent):
    """Agent that sends content for review and notifies assigned reviewers."""

    @property
    def name(self) -> str:
        return "review_requester"

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send content for review and notify reviewers.

        Expected payload:
        {
            "action": "send_for_review",
            "card": {
                "id": "c-12345",
                "title": "Blog post about Redis",
                "meta": {
                    "reviewers": ["alice", "bob"],
                    "priority": "high",
                    "due_date": "2024-01-15"
                }
            },
            "from_column": "In Progress",
            "cockpit": true
        }
        """
        card = payload.get("card", {})
        card_id = card.get("id", "unknown")
        title = card.get("title", "Untitled")
        meta = card.get("meta", {})
        reviewers = meta.get("reviewers", [])
        priority = meta.get("priority", "normal")
        due_date = meta.get("due_date")

        logger.info(f"Requesting review for: {title} (ID: {card_id})")

        try:
            review_request_id = f"review-{card_id}-{int(datetime.now(timezone.utc).timestamp())}"
            review_url = f"http://localhost:8080/review/{review_request_id}"

            # Prepare review request
            review_data = {
                "request_id": review_request_id,
                "card_id": card_id,
                "title": title,
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "reviewers": reviewers if reviewers else ["team"],
                "priority": priority,
                "due_date": due_date,
                "review_url": review_url,
                "status": "pending_review"
            }

            # Store review request in Redis for tracking
            if os.getenv("REDIS_URL"):
                try:
                    import redis
                    import json
                    r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
                    r.setex(
                        f"review:{review_request_id}",
                        86400,  # 24 hour TTL
                        json.dumps(review_data)
                    )
                    logger.info(f"Stored review request: {review_request_id}")
                except Exception as e:
                    logger.warning(f"Could not store review request in Redis: {e}")

            # Send Slack notification to reviewers
            if os.getenv("SLACK_WEBHOOK_URL"):
                self._notify_reviewers(card, review_data)

            # Update Linear/GitHub if configured
            if os.getenv("LINEAR_API_KEY"):
                self._update_linear(card, review_data)
            elif os.getenv("GITHUB_TOKEN"):
                self._update_github(card, review_data)

            return {
                "success": True,
                "card_id": card_id,
                "review_request_id": review_request_id,
                "reviewers": reviewers,
                "review_url": review_url,
                "message": f"Review requested for: {title}"
            }

        except Exception as e:
            logger.error(f"Failed to request review for {card_id}: {e}")
            return {
                "success": False,
                "card_id": card_id,
                "error": str(e),
                "message": f"Failed to request review: {title}"
            }

    def _notify_reviewers(self, card: Dict[str, Any], review_data: Dict[str, Any]) -> None:
        """Send Slack notification to reviewers."""
        try:
            from src.agents.slack_notifier import SlackNotifierAgent
            slack = SlackNotifierAgent()

            reviewers_str = ", ".join(f"@{r}" for r in review_data["reviewers"])
            priority_emoji = "🔴" if review_data["priority"] == "high" else "🟡"

            message = f"{priority_emoji} Review Requested: {card.get('title', 'Untitled')}"
            if reviewers_str and reviewers_str != "@team":
                message += f"\nReviewers: {reviewers_str}"

            slack_payload = {
                "message": message,
                "channel": "#reviews",
                "username": "Review Bot",
                "icon_emoji": ":eyes:",
                "export_type": "Review Request",
                "csv_url": review_data["review_url"],
                "job_id": review_data["request_id"]
            }

            if review_data.get("due_date"):
                slack_payload["meta"] = {"due_date": review_data["due_date"]}

            slack.run(slack_payload)
            logger.info(f"Notified reviewers via Slack")

        except Exception as e:
            logger.warning(f"Could not send Slack notification: {e}")

    def _update_linear(self, card: Dict[str, Any], review_data: Dict[str, Any]) -> None:
        """Update Linear issue with review status."""
        try:
            from src.agents.linear_connector import LinearConnectorAgent
            linear = LinearConnectorAgent()

            linear.run({
                "action": "update_status",
                "issue_id": card.get("linear_id", card.get("id")),
                "status": "In Review",
                "comment": f"Review requested from: {', '.join(review_data['reviewers'])}",
                "labels": ["needs-review", f"priority-{review_data['priority']}"]
            })
            logger.info("Updated Linear issue with review status")

        except Exception as e:
            logger.debug(f"Could not update Linear: {e}")

    def _update_github(self, card: Dict[str, Any], review_data: Dict[str, Any]) -> None:
        """Update GitHub issue/PR with review status."""
        try:
            # This would integrate with GitHub API
            # For now, just log the intent
            logger.info(f"Would update GitHub issue {card.get('id')} with review request")
        except Exception as e:
            logger.debug(f"Could not update GitHub: {e}")


def build_agent() -> ReviewRequesterAgent:
    """Factory function to create ReviewRequesterAgent instance."""
    return ReviewRequesterAgent()


__all__ = ["ReviewRequesterAgent", "build_agent"]