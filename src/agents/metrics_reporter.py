#!/usr/bin/env python3
"""Metrics Reporter Agent - Collects and reports KPIs from the system."""

import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
from collections import defaultdict

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class MetricsReporterAgent(BaseAgent):
    """Agent that collects KPIs and generates reports."""

    @property
    def name(self) -> str:
        return "metrics_reporter"

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect and report KPIs based on the requested metrics.

        Expected payload:
        {
            "action": "report_kpis",
            "card": {
                "id": "c-12345",
                "title": "Weekly Report"
            },
            "report_type": "weekly",  # daily, weekly, monthly
            "metrics": ["completed_tasks", "efficiency", "throughput"],
            "cockpit": true
        }
        """
        card = payload.get("card", {})
        report_type = payload.get("report_type", "daily")
        requested_metrics = payload.get("metrics", ["completed_tasks", "efficiency", "throughput"])

        logger.info(f"Generating {report_type} metrics report")

        try:
            # Collect metrics based on report type
            time_range = self._get_time_range(report_type)
            metrics_data = {}

            if "completed_tasks" in requested_metrics:
                metrics_data["completed_tasks"] = self._get_completed_tasks(time_range)

            if "efficiency" in requested_metrics:
                metrics_data["efficiency"] = self._calculate_efficiency(time_range)

            if "throughput" in requested_metrics:
                metrics_data["throughput"] = self._calculate_throughput(time_range)

            if "daily_plan" in requested_metrics:
                metrics_data["daily_plan"] = self._get_daily_plan()

            if "team_performance" in requested_metrics:
                metrics_data["team_performance"] = self._get_team_performance(time_range)

            # Generate report
            report = self._generate_report(report_type, metrics_data, time_range)

            # Send report via Slack if configured
            if os.getenv("SLACK_WEBHOOK_URL"):
                self._send_report_to_slack(report)

            # Store report in Redis
            report_id = self._store_report(report)

            return {
                "success": True,
                "report_id": report_id,
                "report_type": report_type,
                "metrics": metrics_data,
                "summary": report["summary"],
                "message": f"Generated {report_type} metrics report"
            }

        except Exception as e:
            logger.error(f"Failed to generate metrics report: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to generate {report_type} report"
            }

    def _get_time_range(self, report_type: str) -> Dict[str, datetime]:
        """Calculate time range for the report."""
        now = datetime.now(timezone.utc)

        if report_type == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif report_type == "weekly":
            start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif report_type == "monthly":
            start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        else:
            # Default to last 24 hours
            start = now - timedelta(hours=24)
            end = now

        return {"start": start, "end": end}

    def _get_completed_tasks(self, time_range: Dict[str, datetime]) -> Dict[str, Any]:
        """Get completed tasks metrics from Redis."""
        try:
            if not os.getenv("REDIS_URL"):
                return {"total": 0, "by_user": {}, "by_day": {}}

            import redis
            r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

            completed = {
                "total": 0,
                "by_user": defaultdict(int),
                "by_day": defaultdict(int),
                "by_column": defaultdict(int)
            }

            # Get cards from Published column
            published_cards = r.lrange("wf:board:col:Published", 0, -1)

            for card_json in published_cards:
                try:
                    card = json.loads(card_json)
                    if "updated_at" in card:
                        updated = datetime.fromisoformat(card["updated_at"].replace("Z", "+00:00"))
                        if time_range["start"] <= updated <= time_range["end"]:
                            completed["total"] += 1

                            # By user (if available)
                            user = card.get("meta", {}).get("author", "unassigned")
                            completed["by_user"][user] += 1

                            # By day
                            day_key = updated.strftime("%Y-%m-%d")
                            completed["by_day"][day_key] += 1

                            # By previous column
                            from_column = card.get("from_column", "unknown")
                            completed["by_column"][from_column] += 1

                except Exception as e:
                    logger.debug(f"Error parsing card: {e}")

            # Get approval metrics if available
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            approval_metrics = r.hgetall(f"metrics:approvals:{today}")
            if approval_metrics:
                completed["approvals_today"] = sum(int(v) for v in approval_metrics.values())
                completed["approvers"] = {k: int(v) for k, v in approval_metrics.items()}

            return dict(completed)

        except Exception as e:
            logger.error(f"Error getting completed tasks: {e}")
            return {"total": 0, "error": str(e)}

    def _calculate_efficiency(self, time_range: Dict[str, datetime]) -> Dict[str, Any]:
        """Calculate efficiency metrics."""
        try:
            if not os.getenv("REDIS_URL"):
                return {"overall": 0, "by_user": {}}

            import redis
            r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

            efficiency = {
                "overall": 0,
                "by_user": {},
                "cycle_times": []
            }

            # Calculate average cycle time (Backlog → Published)
            published_cards = r.lrange("wf:board:col:Published", 0, -1)
            cycle_times = []

            for card_json in published_cards:
                try:
                    card = json.loads(card_json)
                    if "created_at" in card and "updated_at" in card:
                        created = datetime.fromisoformat(card["created_at"].replace("Z", "+00:00"))
                        updated = datetime.fromisoformat(card["updated_at"].replace("Z", "+00:00"))

                        if time_range["start"] <= updated <= time_range["end"]:
                            cycle_time_hours = (updated - created).total_seconds() / 3600
                            cycle_times.append(cycle_time_hours)

                except Exception as e:
                    logger.debug(f"Error calculating cycle time: {e}")

            if cycle_times:
                avg_cycle_time = sum(cycle_times) / len(cycle_times)
                # Efficiency = (ideal_time / actual_time) * 100
                # Assuming ideal cycle time is 8 hours
                ideal_time = 8
                efficiency["overall"] = min(100, (ideal_time / avg_cycle_time) * 100) if avg_cycle_time > 0 else 0
                efficiency["avg_cycle_time_hours"] = round(avg_cycle_time, 2)
                efficiency["min_cycle_time"] = round(min(cycle_times), 2)
                efficiency["max_cycle_time"] = round(max(cycle_times), 2)

            return efficiency

        except Exception as e:
            logger.error(f"Error calculating efficiency: {e}")
            return {"overall": 0, "error": str(e)}

    def _calculate_throughput(self, time_range: Dict[str, datetime]) -> Dict[str, Any]:
        """Calculate throughput metrics."""
        try:
            completed_tasks = self._get_completed_tasks(time_range)
            days = max(1, (time_range["end"] - time_range["start"]).days)

            throughput = {
                "total": completed_tasks.get("total", 0),
                "daily_average": round(completed_tasks.get("total", 0) / days, 2),
                "by_column": {}
            }

            # Get current queue depths
            if os.getenv("REDIS_URL"):
                import redis
                r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

                columns = ["Backlog", "In Progress", "Waiting Approval", "Scheduled", "Published"]
                for col in columns:
                    count = r.llen(f"wf:board:col:{col}")
                    throughput["by_column"][col] = count

            return throughput

        except Exception as e:
            logger.error(f"Error calculating throughput: {e}")
            return {"total": 0, "error": str(e)}

    def _get_daily_plan(self) -> List[Dict[str, Any]]:
        """Get today's planned tasks."""
        try:
            if not os.getenv("REDIS_URL"):
                return []

            import redis
            r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

            today = datetime.now(timezone.utc).date()
            daily_tasks = []

            # Check Scheduled column for today's tasks
            scheduled_cards = r.lrange("wf:board:col:Scheduled", 0, -1)

            for card_json in scheduled_cards:
                try:
                    card = json.loads(card_json)
                    scheduled_time = card.get("meta", {}).get("scheduled_time")

                    if scheduled_time:
                        scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
                        if scheduled_dt.date() == today:
                            daily_tasks.append({
                                "id": card.get("id"),
                                "title": card.get("title"),
                                "scheduled_time": scheduled_time,
                                "assignee": card.get("meta", {}).get("assignee", "unassigned")
                            })

                except Exception as e:
                    logger.debug(f"Error parsing scheduled card: {e}")

            # Sort by scheduled time
            daily_tasks.sort(key=lambda x: x.get("scheduled_time", ""))

            return daily_tasks

        except Exception as e:
            logger.error(f"Error getting daily plan: {e}")
            return []

    def _get_team_performance(self, time_range: Dict[str, datetime]) -> Dict[str, Any]:
        """Get team performance metrics."""
        completed = self._get_completed_tasks(time_range)

        performance = {
            "top_performers": [],
            "total_completed": completed.get("total", 0)
        }

        # Sort users by completed tasks
        by_user = completed.get("by_user", {})
        if by_user:
            sorted_users = sorted(by_user.items(), key=lambda x: x[1], reverse=True)
            performance["top_performers"] = [
                {"user": user, "completed": count}
                for user, count in sorted_users[:5]
            ]

        return performance

    def _generate_report(self, report_type: str, metrics: Dict[str, Any], time_range: Dict[str, datetime]) -> Dict[str, Any]:
        """Generate formatted report."""
        report = {
            "id": f"report-{int(datetime.now(timezone.utc).timestamp())}",
            "type": report_type,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "time_range": {
                "start": time_range["start"].isoformat(),
                "end": time_range["end"].isoformat()
            },
            "metrics": metrics,
            "summary": ""
        }

        # Build summary
        summary_parts = []

        if "completed_tasks" in metrics:
            total = metrics["completed_tasks"].get("total", 0)
            summary_parts.append(f"✅ {total} tasks completed")

        if "efficiency" in metrics:
            eff = metrics["efficiency"].get("overall", 0)
            summary_parts.append(f"⚡ {eff:.1f}% efficiency")

        if "throughput" in metrics:
            avg = metrics["throughput"].get("daily_average", 0)
            summary_parts.append(f"📊 {avg:.1f} tasks/day average")

        report["summary"] = " | ".join(summary_parts)

        return report

    def _send_report_to_slack(self, report: Dict[str, Any]) -> None:
        """Send report to Slack."""
        try:
            from src.agents.slack_notifier import SlackNotifierAgent
            slack = SlackNotifierAgent()

            # Format report for Slack
            message = f"📊 *{report['type'].title()} Metrics Report*\n"
            message += f"_{report['summary']}_\n\n"

            metrics = report.get("metrics", {})

            # Add completed tasks details
            if "completed_tasks" in metrics:
                ct = metrics["completed_tasks"]
                message += "*Completed Tasks:*\n"
                message += f"• Total: {ct.get('total', 0)}\n"

                top_users = sorted(
                    ct.get("by_user", {}).items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:3]
                if top_users:
                    message += "• Top performers:\n"
                    for user, count in top_users:
                        message += f"  - {user}: {count} tasks\n"

            # Add efficiency details
            if "efficiency" in metrics:
                eff = metrics["efficiency"]
                message += f"\n*Efficiency:*\n"
                message += f"• Overall: {eff.get('overall', 0):.1f}%\n"
                if "avg_cycle_time_hours" in eff:
                    message += f"• Avg cycle time: {eff['avg_cycle_time_hours']} hours\n"

            slack.run({
                "message": message,
                "channel": "#metrics",
                "username": "Metrics Bot",
                "icon_emoji": ":chart_with_upwards_trend:",
                "export_type": "Metrics Report"
            })

            logger.info("Sent metrics report to Slack")

        except Exception as e:
            logger.warning(f"Could not send report to Slack: {e}")

    def _store_report(self, report: Dict[str, Any]) -> str:
        """Store report in Redis."""
        try:
            if not os.getenv("REDIS_URL"):
                return report["id"]

            import redis
            r = redis.Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

            # Store report with 30-day TTL
            r.setex(
                f"report:{report['id']}",
                2592000,
                json.dumps(report)
            )

            # Add to reports list
            r.lpush("reports:all", report["id"])
            r.ltrim("reports:all", 0, 99)  # Keep last 100 reports

            logger.info(f"Stored report: {report['id']}")
            return report["id"]

        except Exception as e:
            logger.warning(f"Could not store report: {e}")
            return report["id"]


def build_agent() -> MetricsReporterAgent:
    """Factory function to create MetricsReporterAgent instance."""
    return MetricsReporterAgent()


__all__ = ["MetricsReporterAgent", "build_agent"]