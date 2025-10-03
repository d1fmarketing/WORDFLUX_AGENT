#!/usr/bin/env python3
"""Slack notification agent for CSV exports and alerts."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

import requests

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class SlackNotifierAgent(BaseAgent):
    """Agent that sends notifications to Slack with CSV export URLs."""

    def __init__(self, webhook_url: str | None = None):
        """
        Initialize Slack notifier agent.

        Args:
            webhook_url: Slack webhook URL (defaults to SLACK_WEBHOOK_URL env var)
        """
        self.webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
        if not self.webhook_url:
            logger.warning("No Slack webhook URL configured")

    @property
    def name(self) -> str:
        return "slack_notifier"

    def run(self, payload: dict) -> dict:
        """
        Send notification to Slack.

        Expected payload:
        {
            "channel": "#exports",  # Optional, uses webhook default if not specified
            "message": "Your CSV export is ready",
            "csv_url": "https://s3.amazonaws.com/...",  # Optional
            "export_type": "stripe_disputes",  # Optional
            "record_count": 150,  # Optional
            "username": "WordFlux Bot",  # Optional
            "icon_emoji": ":robot_face:"  # Optional
        }
        """
        if not self.webhook_url:
            logger.error("Cannot send Slack notification - no webhook URL configured")
            return {
                "success": False,
                "error": "No Slack webhook URL configured"
            }

        try:
            # Build Slack message
            message = payload.get("message", "Notification from WordFlux")
            channel = payload.get("channel")
            username = payload.get("username", "WordFlux Bot")
            icon_emoji = payload.get("icon_emoji", ":robot_face:")

            # Build rich message with CSV export details if provided
            slack_data = {
                "username": username,
                "icon_emoji": icon_emoji,
                "text": message
            }

            if channel:
                slack_data["channel"] = channel

            # Add structured attachment for CSV export or other notifications
            csv_url = payload.get("csv_url")
            export_type = payload.get("export_type", "Export")

            # Build rich attachment
            attachments = []

            if csv_url:
                # CSV export notification
                record_count = payload.get("record_count")
                file_size = payload.get("file_size")
                duration = payload.get("duration_seconds")
                job_id = payload.get("job_id")

                attachment = {
                    "color": "good",
                    "title": f"✅ {export_type} Complete",
                    "title_link": csv_url,
                    "fields": []
                }

                # Add metrics fields
                if record_count is not None:
                    attachment["fields"].append({
                        "title": "📊 Records",
                        "value": f"{record_count:,}",
                        "short": True
                    })

                if file_size:
                    # Convert bytes to human-readable
                    size_mb = file_size / (1024 * 1024)
                    attachment["fields"].append({
                        "title": "💾 Size",
                        "value": f"{size_mb:.2f} MB",
                        "short": True
                    })

                if duration:
                    attachment["fields"].append({
                        "title": "⏱️ Duration",
                        "value": f"{duration:.1f}s",
                        "short": True
                    })

                if job_id:
                    attachment["fields"].append({
                        "title": "🔖 Job ID",
                        "value": f"`{job_id}`",
                        "short": True
                    })

                # Add download button
                attachment["fields"].append({
                    "title": "📥 Download",
                    "value": f"<{csv_url}|Download CSV>",
                    "short": False
                })

                # Add timestamp and footer
                from datetime import datetime, timezone
                attachment["ts"] = int(datetime.now(timezone.utc).timestamp())
                attachment["footer"] = "WordFlux Agent System"
                attachment["footer_icon"] = "https://platform.slack-edge.com/img/default_application_icon.png"

                attachments.append(attachment)

            elif payload.get("error"):
                # Error notification
                error = payload.get("error")
                job_id = payload.get("job_id")
                agent_name = payload.get("agent")

                attachment = {
                    "color": "danger",
                    "title": f"❌ {export_type} Failed",
                    "text": f"```{error}```",
                    "fields": []
                }

                if job_id:
                    attachment["fields"].append({
                        "title": "Job ID",
                        "value": f"`{job_id}`",
                        "short": True
                    })

                if agent_name:
                    attachment["fields"].append({
                        "title": "Agent",
                        "value": agent_name,
                        "short": True
                    })

                from datetime import datetime, timezone
                attachment["ts"] = int(datetime.now(timezone.utc).timestamp())
                attachment["footer"] = "WordFlux Agent System"

                attachments.append(attachment)

            if attachments:
                slack_data["attachments"] = attachments

            # Send to Slack
            response = requests.post(
                self.webhook_url,
                json=slack_data,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"Slack notification sent successfully")
                return {
                    "success": True,
                    "message": "Notification sent to Slack"
                }
            else:
                logger.error(f"Slack webhook returned status {response.status_code}: {response.text}")
                return {
                    "success": False,
                    "error": f"Slack webhook error: {response.status_code}"
                }

        except requests.RequestException as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Unexpected error sending Slack notification: {e}")
            return {
                "success": False,
                "error": f"Unexpected error: {e}"
            }


def build_agent() -> SlackNotifierAgent:
    """Factory function to create SlackNotifierAgent instance."""
    return SlackNotifierAgent()


__all__ = ["SlackNotifierAgent", "build_agent"]