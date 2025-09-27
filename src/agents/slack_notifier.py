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

            # Add attachment for CSV export details
            csv_url = payload.get("csv_url")
            if csv_url:
                export_type = payload.get("export_type", "CSV Export")
                record_count = payload.get("record_count")

                attachment = {
                    "color": "good",
                    "title": f"{export_type} Ready",
                    "fields": []
                }

                if record_count is not None:
                    attachment["fields"].append({
                        "title": "Records",
                        "value": str(record_count),
                        "short": True
                    })

                attachment["fields"].append({
                    "title": "Download URL",
                    "value": f"<{csv_url}|Download CSV>",
                    "short": False
                })

                # Add timestamp
                import datetime
                attachment["ts"] = int(datetime.datetime.utcnow().timestamp())
                attachment["footer"] = "WordFlux Export"

                slack_data["attachments"] = [attachment]

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