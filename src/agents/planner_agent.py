#!/usr/bin/env python3
"""Planner Agent - Autonomously creates and moves cards through workflow."""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List

from src.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class PlannerAgent(BaseAgent):
    """Agent that autonomously creates cards from templates and moves them respecting WIP limits."""

    def __init__(self):
        super().__init__("planner_agent")

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create cards from templates and move them through workflow respecting WIP limits.

        Expected payload:
        {
            "mode": "create" | "move" | "full",  # What to do
            "template_name": "daily_content",     # Optional: specific template
            "wip_limit": 2                        # Optional: override default WIP
        }

        Returns:
        {
            "success": True,
            "created": N,      # Number of cards created
            "moved": M,        # Number of cards moved
            "templates_used": [...],
            "cards": [...]     # Created/moved cards
        }
        """
        mode = payload.get("mode", "full")  # full = create + move
        wip_limit = int(payload.get("wip_limit", os.getenv("WF_WIP_LIMIT", "2")))

        created_count = 0
        moved_count = 0
        cards_created = []
        cards_moved = []

        try:
            # Import Redis client from cockpit (if running via cockpit)
            # Otherwise use standard queue
            redis_client = self._get_redis_client()

            if mode in ["create", "full"]:
                # Create cards from templates
                template_name = payload.get("template_name", "daily_content")
                templates = self._get_templates(template_name)

                for template in templates:
                    card = self._create_card_from_template(template, redis_client)
                    if card:
                        cards_created.append(card)
                        created_count += 1
                        logger.info(f"Created card: {card['title']} (ID: {card['id']})")

            if mode in ["move", "full"]:
                # Move cards from "Backlog" to "In Progress" respecting WIP
                moved = self._move_cards_to_production(redis_client, wip_limit)
                cards_moved = moved
                moved_count = len(moved)

            result = {
                "success": True,
                "created": created_count,
                "moved": moved_count,
                "templates_used": [t["name"] for t in self._get_templates(payload.get("template_name", "daily_content"))],
                "cards": cards_created + cards_moved,
                "wip_limit": wip_limit,
                "message": f"Planner: created {created_count}, moved {moved_count} cards"
            }

            logger.info(f"Planner completed: {result['message']}")
            return result

        except Exception as e:
            logger.error(f"Planner failed: {e}", exc_info=True)
            return {
                "success": False,
                "created": created_count,
                "moved": moved_count,
                "error": str(e),
                "message": f"Planner failed: {e}"
            }

    def _get_redis_client(self):
        """Get Redis client for board operations."""
        try:
            import redis
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            return redis.from_url(redis_url, decode_responses=True)
        except Exception as e:
            logger.error(f"Could not connect to Redis: {e}")
            raise

    def _get_templates(self, template_name: str) -> List[Dict[str, Any]]:
        """Get card templates. In production, this would read from config/Redis."""
        templates = {
            "daily_content": [
                {
                    "name": "daily_blog_post",
                    "title": f"Blog post: {datetime.now().strftime('%Y-%m-%d')}",
                    "description": "Daily content piece for blog",
                    "meta": {"type": "blog", "priority": "normal", "template": "daily_content"}
                },
                {
                    "name": "social_media_update",
                    "title": f"Social media post: {datetime.now().strftime('%A')}",
                    "description": "Weekly social media content",
                    "meta": {"type": "social", "priority": "high", "template": "daily_content"}
                }
            ],
            "weekly_newsletter": [
                {
                    "name": "newsletter",
                    "title": f"Newsletter: Week of {datetime.now().strftime('%b %d')}",
                    "description": "Weekly newsletter compilation",
                    "meta": {"type": "newsletter", "priority": "high", "template": "weekly_newsletter"}
                }
            ]
        }

        # Return templates for requested name, or all daily templates by default
        return templates.get(template_name, templates["daily_content"])

    def _create_card_from_template(
        self,
        template: Dict[str, Any],
        redis_client
    ) -> Dict[str, Any]:
        """Create a card in the Backlog column from a template."""
        try:
            card = {
                "id": f"c-{uuid.uuid4().hex[:8]}",
                "title": template["title"],
                "description": template.get("description", ""),
                "meta": template.get("meta", {}),
                "column": "Backlog",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "created_by": "planner_agent"
            }

            # Push to Backlog column in Redis
            import json
            redis_client.lpush("wf:board:col:Backlog", json.dumps(card))

            # Emit event
            event_data = json.dumps({
                "type": "card_created",
                "card": card,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            redis_client.publish("wf:events", event_data)
            redis_client.lpush("wf:events:recent", event_data)
            redis_client.ltrim("wf:events:recent", 0, 199)  # Keep last 200

            return card

        except Exception as e:
            logger.error(f"Failed to create card from template {template.get('name')}: {e}")
            return None

    def _move_cards_to_production(
        self,
        redis_client,
        wip_limit: int
    ) -> List[Dict[str, Any]]:
        """
        Move cards from Backlog to In Progress, respecting WIP limit.

        Returns list of moved cards.
        """
        import json
        moved_cards = []

        try:
            # Get current count in "In Progress"
            in_progress_key = "wf:board:col:In Progress"
            current_count = redis_client.llen(in_progress_key)

            logger.info(f"In Progress: {current_count}/{wip_limit} cards")

            # Calculate how many cards we can move
            available_slots = max(0, wip_limit - current_count)
            if available_slots == 0:
                logger.info("WIP limit reached, not moving cards")
                return moved_cards

            # Get cards from Backlog (oldest first = rightmost in Redis list)
            backlog_key = "wf:board:col:Backlog"
            backlog_count = redis_client.llen(backlog_key)

            if backlog_count == 0:
                logger.info("No cards in Backlog to move")
                return moved_cards

            # Move up to available_slots cards
            cards_to_move = min(available_slots, backlog_count)
            logger.info(f"Moving {cards_to_move} cards from Backlog to In Progress")

            for _ in range(cards_to_move):
                # Pop from right (oldest card) of Backlog
                card_json = redis_client.rpop(backlog_key)
                if not card_json:
                    break

                try:
                    card = json.loads(card_json)
                    card["column"] = "In Progress"
                    card["updated_at"] = datetime.now(timezone.utc).isoformat()
                    card["moved_by"] = "planner_agent"

                    # Push to In Progress
                    redis_client.lpush(in_progress_key, json.dumps(card))

                    # Emit event
                    event_data = json.dumps({
                        "type": "card_moved",
                        "card": card,
                        "from": "Backlog",
                        "to": "In Progress",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                    redis_client.publish("wf:events", event_data)
                    redis_client.lpush("wf:events:recent", event_data)
                    redis_client.ltrim("wf:events:recent", 0, 199)

                    moved_cards.append(card)
                    logger.info(f"Moved card: {card['title']} to In Progress")

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid card JSON in Backlog: {e}")
                    continue

            return moved_cards

        except Exception as e:
            logger.error(f"Failed to move cards to production: {e}")
            return moved_cards


def build_agent() -> PlannerAgent:
    """Factory function to create PlannerAgent instance."""
    return PlannerAgent()


__all__ = ["PlannerAgent", "build_agent"]