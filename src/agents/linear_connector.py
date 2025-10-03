#!/usr/bin/env python3
"""Linear board connector agent for updating cards with job status."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import requests

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class LinearConnectorAgent(BaseAgent):
    """Agent that updates Linear cards with job status and artifacts."""

    def __init__(self, api_key: str | None = None):
        """
        Initialize Linear connector agent.

        Args:
            api_key: Linear API key (defaults to LINEAR_API_KEY env var)
        """
        super().__init__(name="linear_connector")
        self.api_key = api_key or os.getenv("LINEAR_API_KEY")
        self.api_url = "https://api.linear.app/graphql"
        self.team_id = os.getenv("LINEAR_TEAM_ID")

        if not self.api_key:
            logger.warning("No Linear API key configured")

    def run(self, payload: dict) -> dict:
        """
        Update Linear card with job status.

        Expected payload:
        {
            "issue_id": "WF-123",  # Linear issue identifier
            "status": "success",  # success/error/in_progress
            "message": "Export completed",
            "artifact_url": "https://s3.amazonaws.com/...",  # Optional
            "job_id": "abc123",  # Optional
            "metrics": {  # Optional
                "duration_seconds": 45,
                "record_count": 150,
                "file_size": 2048576
            }
        }
        """
        if not self.api_key:
            logger.error("Cannot update Linear card - no API key configured")
            return {
                "success": False,
                "error": "No Linear API key configured"
            }

        try:
            issue_id = payload.get("issue_id")
            if not issue_id:
                return {
                    "success": False,
                    "error": "Missing issue_id in payload"
                }

            status = payload.get("status", "success")
            message = payload.get("message", "Job completed")
            artifact_url = payload.get("artifact_url")
            job_id = payload.get("job_id")
            metrics = payload.get("metrics", {})

            # First, get the issue to find its database ID
            issue_data = self._get_issue(issue_id)
            if not issue_data:
                return {
                    "success": False,
                    "error": f"Issue {issue_id} not found"
                }

            issue_db_id = issue_data.get("id")

            # Build comment content
            comment_body = self._build_comment(
                status=status,
                message=message,
                artifact_url=artifact_url,
                job_id=job_id,
                metrics=metrics
            )

            # Add comment to issue
            comment_result = self._add_comment(issue_db_id, comment_body)

            if comment_result:
                # Optionally update issue state based on status
                if status == "success" and os.getenv("LINEAR_AUTO_COMPLETE") == "true":
                    self._update_issue_state(issue_db_id, "completed")
                elif status == "error" and os.getenv("LINEAR_AUTO_REOPEN") == "true":
                    self._update_issue_state(issue_db_id, "started")

                logger.info(f"Linear card {issue_id} updated successfully")
                return {
                    "success": True,
                    "message": f"Linear card {issue_id} updated",
                    "comment_id": comment_result.get("id")
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to add comment to Linear issue"
                }

        except Exception as e:
            logger.error(f"Failed to update Linear card: {e}")
            return {
                "success": False,
                "error": f"Unexpected error: {e}"
            }

    def _get_issue(self, issue_identifier: str) -> Optional[Dict[str, Any]]:
        """Get Linear issue by identifier."""
        query = """
        query($identifier: String!) {
            issue(id: $identifier) {
                id
                identifier
                title
                state {
                    name
                }
            }
        }
        """

        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": self.api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "query": query,
                    "variables": {"identifier": issue_identifier}
                },
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("data", {}).get("issue")
            else:
                logger.error(f"Linear API error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Failed to get Linear issue: {e}")
            return None

    def _add_comment(self, issue_id: str, body: str) -> Optional[Dict[str, Any]]:
        """Add comment to Linear issue."""
        mutation = """
        mutation($issueId: String!, $body: String!) {
            commentCreate(
                input: {
                    issueId: $issueId
                    body: $body
                }
            ) {
                success
                comment {
                    id
                    createdAt
                }
            }
        }
        """

        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": self.api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "query": mutation,
                    "variables": {
                        "issueId": issue_id,
                        "body": body
                    }
                },
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                result = data.get("data", {}).get("commentCreate", {})
                if result.get("success"):
                    return result.get("comment")
            else:
                logger.error(f"Linear API error: {response.status_code} - {response.text}")

            return None

        except Exception as e:
            logger.error(f"Failed to add Linear comment: {e}")
            return None

    def _update_issue_state(self, issue_id: str, state_name: str) -> bool:
        """Update Linear issue state."""
        # This would require fetching workflow states first
        # Simplified for now - you'd need to map state names to state IDs
        logger.info(f"Would update issue {issue_id} to state {state_name}")
        return True

    def _build_comment(
        self,
        status: str,
        message: str,
        artifact_url: Optional[str] = None,
        job_id: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build formatted comment for Linear."""
        # Status emoji
        status_emoji = {
            "success": "✅",
            "error": "❌",
            "in_progress": "⏳"
        }.get(status, "ℹ️")

        # Build comment parts
        parts = [
            f"## {status_emoji} WordFlux Job Update",
            f"\n**Status:** {status.title()}",
            f"**Message:** {message}"
        ]

        if job_id:
            parts.append(f"**Job ID:** `{job_id}`")

        # Add metrics if provided
        if metrics:
            metrics_parts = []
            if "duration_seconds" in metrics:
                duration = metrics["duration_seconds"]
                metrics_parts.append(f"⏱️ Duration: {duration:.1f}s")
            if "record_count" in metrics:
                count = metrics["record_count"]
                metrics_parts.append(f"📊 Records: {count:,}")
            if "file_size" in metrics:
                size_mb = metrics["file_size"] / (1024 * 1024)
                metrics_parts.append(f"💾 Size: {size_mb:.2f} MB")

            if metrics_parts:
                parts.append("\n**Metrics:**")
                parts.extend([f"- {m}" for m in metrics_parts])

        # Add artifact link
        if artifact_url:
            parts.append(f"\n📥 **[Download Artifact]({artifact_url})**")

        # Add footer
        parts.append("\n---")
        parts.append("*Generated by WordFlux Agent System*")

        return "\n".join(parts)


def build_agent() -> LinearConnectorAgent:
    """Factory function to create LinearConnectorAgent instance."""
    return LinearConnectorAgent()


__all__ = ["LinearConnectorAgent", "build_agent"]