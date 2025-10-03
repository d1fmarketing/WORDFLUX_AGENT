#!/usr/bin/env python3
"""Content Publisher Agent - Handles publishing content from the cockpit."""

import logging
from datetime import datetime, timezone
from typing import Dict, Any

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ContentPublisherAgent(BaseAgent):
    """Agent that publishes content when triggered from the cockpit."""

    @property
    def name(self) -> str:
        return "content_publisher"

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Publish content to various platforms.

        Expected payload:
        {
            "action": "publish_now",
            "card": {
                "id": "c-12345",
                "title": "Blog post about Redis",
                "meta": {"platform": "blog", "scheduled_time": "..."}
            },
            "from_column": "Scheduled",
            "cockpit": true
        }
        """
        card = payload.get("card", {})
        card_id = card.get("id", "unknown")
        title = card.get("title", "Untitled")
        meta = card.get("meta", {})
        platform = meta.get("platform", "default")

        logger.info(f"Publishing content: {title} (ID: {card_id}) to {platform}")

        try:
            # Simulate publishing to different platforms
            published_url = None
            publish_time = datetime.now(timezone.utc).isoformat()

            if platform == "blog":
                # Simulate blog publishing
                published_url = f"https://blog.example.com/posts/{card_id}"
                logger.info(f"Published to blog: {published_url}")

            elif platform == "social":
                # Simulate social media publishing
                published_url = f"https://twitter.com/status/{card_id}"
                logger.info(f"Published to social media: {published_url}")

            elif platform == "email":
                # Simulate email campaign
                published_url = f"https://campaigns.example.com/{card_id}"
                logger.info(f"Email campaign sent: {published_url}")

            else:
                # Default publishing
                published_url = f"https://content.example.com/{card_id}"
                logger.info(f"Published to default platform: {published_url}")

            # Return success with published details
            result = {
                "success": True,
                "card_id": card_id,
                "title": title,
                "published_url": published_url,
                "publish_time": publish_time,
                "platform": platform,
                "message": f"Successfully published: {title}"
            }

            # If Slack webhook is configured, send a notification
            import os
            if os.getenv("SLACK_WEBHOOK_URL"):
                self._send_slack_notification(card, published_url)

            return result

        except Exception as e:
            logger.error(f"Failed to publish content {card_id}: {e}")
            return {
                "success": False,
                "card_id": card_id,
                "error": str(e),
                "message": f"Failed to publish: {title}"
            }

    def _send_slack_notification(self, card: Dict[str, Any], url: str) -> None:
        """Send a Slack notification about published content."""
        try:
            from src.agents.slack_notifier import SlackNotifierAgent
            slack = SlackNotifierAgent()
            slack.run({
                "message": f"🚀 Published: {card.get('title', 'Untitled')}",
                "channel": "#content-published",
                "csv_url": url,
                "export_type": "Content Publication",
                "username": "Publisher Bot",
                "icon_emoji": ":rocket:"
            })
        except Exception as e:
            logger.warning(f"Could not send Slack notification: {e}")


def build_agent() -> ContentPublisherAgent:
    """Factory function to create ContentPublisherAgent instance."""
    return ContentPublisherAgent()


__all__ = ["ContentPublisherAgent", "build_agent"]