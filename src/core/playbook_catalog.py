"""Playbook catalog - Registry of available playbooks with ID-to-path mapping.

This module provides a simple catalog system for playbooks, allowing them to be
referenced by short IDs rather than file paths. This makes it easy to trigger
playbooks via chat commands like "execute playbook release-train".

Usage:
    from src.core.playbook_catalog import resolve_playbook_path, list_playbooks

    # Resolve ID to path
    path = resolve_playbook_path("release-train")

    # List all available playbooks
    playbooks = list_playbooks()
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Base directory for playbooks
PLAYBOOKS_DIR = Path(__file__).parent.parent.parent / "playbooks"

# Playbook catalog: ID → relative path from PLAYBOOKS_DIR
# IDs should be kebab-case, lowercase, descriptive
PLAYBOOK_CATALOG: Dict[str, str] = {
    # Existing playbooks
    "release-train": "release_train.yaml",
    "test-workflow": "test_workflow.yaml",

    # New playbooks
    "crm-hygiene": "crm_hygiene.yaml",
    "content-loop": "content_loop.yaml",
}

# Metadata for each playbook (optional, for UI/documentation)
PLAYBOOK_METADATA: Dict[str, Dict[str, str]] = {
    "release-train": {
        "name": "Release Train",
        "description": "Complete release workflow with Stripe export, Slack notifications, and Linear updates",
        "tags": ["release", "stripe", "automated"],
        "category": "deployment"
    },
    "test-workflow": {
        "name": "Test Workflow",
        "description": "Simple test workflow with echo agents for development",
        "tags": ["test", "demo"],
        "category": "testing"
    },
    "crm-hygiene": {
        "name": "CRM Hygiene",
        "description": "Clean up stale cards and archive old items",
        "tags": ["maintenance", "automation"],
        "category": "maintenance"
    },
    "content-loop": {
        "name": "Content Loop",
        "description": "Autonomous content creation and progression",
        "tags": ["content", "automation", "planner"],
        "category": "content"
    },
}


def resolve_playbook_path(playbook_id: str) -> Optional[str]:
    """
    Resolve playbook ID to absolute file path.

    Args:
        playbook_id: Playbook ID (e.g., "release-train")

    Returns:
        Absolute path to playbook YAML file, or None if not found

    Examples:
        >>> resolve_playbook_path("release-train")
        "/home/ubuntu/playbooks/release_train.yaml"

        >>> resolve_playbook_path("unknown")
        None
    """
    relative_path = PLAYBOOK_CATALOG.get(playbook_id)
    if not relative_path:
        logger.warning(f"Playbook ID '{playbook_id}' not found in catalog")
        return None

    absolute_path = PLAYBOOKS_DIR / relative_path

    # Verify file exists
    if not absolute_path.exists():
        logger.error(f"Playbook file not found: {absolute_path}")
        return None

    return str(absolute_path)


def list_playbooks() -> List[Dict[str, str]]:
    """
    List all available playbooks with metadata.

    Returns:
        List of playbook dicts with id, name, description, tags, category

    Example:
        >>> playbooks = list_playbooks()
        >>> for p in playbooks:
        ...     print(f"{p['id']}: {p['name']}")
        release-train: Release Train
        crm-hygiene: CRM Hygiene
    """
    playbooks = []

    for playbook_id, relative_path in PLAYBOOK_CATALOG.items():
        metadata = PLAYBOOK_METADATA.get(playbook_id, {})

        playbook_info = {
            "id": playbook_id,
            "name": metadata.get("name", playbook_id.replace("-", " ").title()),
            "description": metadata.get("description", ""),
            "tags": metadata.get("tags", []),
            "category": metadata.get("category", "other"),
            "path": str(PLAYBOOKS_DIR / relative_path)
        }

        playbooks.append(playbook_info)

    # Sort by category, then name
    playbooks.sort(key=lambda p: (p["category"], p["name"]))

    return playbooks


def get_playbook_info(playbook_id: str) -> Optional[Dict[str, str]]:
    """
    Get metadata for a specific playbook.

    Args:
        playbook_id: Playbook ID

    Returns:
        Dict with playbook metadata, or None if not found

    Example:
        >>> info = get_playbook_info("release-train")
        >>> print(info["description"])
        Complete release workflow...
    """
    if playbook_id not in PLAYBOOK_CATALOG:
        return None

    metadata = PLAYBOOK_METADATA.get(playbook_id, {})
    relative_path = PLAYBOOK_CATALOG[playbook_id]

    return {
        "id": playbook_id,
        "name": metadata.get("name", playbook_id.replace("-", " ").title()),
        "description": metadata.get("description", ""),
        "tags": metadata.get("tags", []),
        "category": metadata.get("category", "other"),
        "path": str(PLAYBOOKS_DIR / relative_path)
    }


def register_playbook(playbook_id: str, relative_path: str, metadata: Optional[Dict[str, str]] = None) -> None:
    """
    Register a new playbook dynamically.

    Args:
        playbook_id: Unique ID for the playbook
        relative_path: Path relative to PLAYBOOKS_DIR
        metadata: Optional metadata dict

    Example:
        >>> register_playbook(
        ...     "my-workflow",
        ...     "my_workflow.yaml",
        ...     {"name": "My Workflow", "description": "Custom workflow"}
        ... )
    """
    if playbook_id in PLAYBOOK_CATALOG:
        logger.warning(f"Playbook ID '{playbook_id}' already registered, overwriting")

    PLAYBOOK_CATALOG[playbook_id] = relative_path

    if metadata:
        PLAYBOOK_METADATA[playbook_id] = metadata

    logger.info(f"Registered playbook '{playbook_id}' → {relative_path}")


__all__ = [
    "resolve_playbook_path",
    "list_playbooks",
    "get_playbook_info",
    "register_playbook",
    "PLAYBOOK_CATALOG",
    "PLAYBOOK_METADATA",
]
