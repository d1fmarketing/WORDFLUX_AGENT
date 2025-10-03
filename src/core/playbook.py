#!/usr/bin/env python3
"""Playbook models and execution engine for orchestrating workflows."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class RetryStrategy(str, Enum):
    """Retry strategies for step execution."""
    NONE = "none"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class StepRetry(BaseModel):
    """Retry configuration for a step."""
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff: RetryStrategy = Field(default=RetryStrategy.EXPONENTIAL)
    delay_seconds: int = Field(default=1, ge=1, le=300)


class StepLock(BaseModel):
    """Lock configuration for a step."""
    resource: str = Field(..., description="Resource to lock (e.g., 'deploy:production')")
    timeout: int = Field(default=300, ge=1, le=3600)
    wait: bool = Field(default=True, description="Wait for lock or fail immediately")


class PlaybookStep(BaseModel):
    """Individual step in a playbook."""
    name: str = Field(..., description="Step name")
    agent: str = Field(..., description="Agent to execute")
    with_: Dict[str, Any] = Field(default_factory=dict, alias="with", description="Agent payload")
    retry: Optional[StepRetry] = Field(default=None, description="Retry configuration")
    lock: Optional[StepLock] = Field(default=None, description="Lock configuration")
    continue_on_error: bool = Field(default=False, description="Continue even if step fails")
    condition: Optional[str] = Field(default=None, description="Jinja2 condition to evaluate")
    timeout_seconds: int = Field(default=300, ge=1, le=3600)

    class Config:
        allow_population_by_field_name = True

    @validator('agent')
    def validate_agent(cls, v):
        """Ensure agent name is valid."""
        if not v or not v.strip():
            raise ValueError("Agent name cannot be empty")
        return v.strip()


class Playbook(BaseModel):
    """Playbook definition for orchestrating multiple agents."""
    name: str = Field(..., description="Playbook name")
    description: Optional[str] = Field(default=None, description="Playbook description")
    version: str = Field(default="1.0", description="Playbook version")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="Input parameters")
    steps: List[PlaybookStep] = Field(..., min_items=1, description="Steps to execute")
    on_failure: Optional[str] = Field(default=None, description="Handler for failures")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")

    @validator('steps')
    def validate_steps(cls, v):
        """Ensure step names are unique."""
        names = [step.name for step in v]
        if len(names) != len(set(names)):
            raise ValueError("Step names must be unique within a playbook")
        return v

    @classmethod
    def from_yaml(cls, yaml_content: str) -> Playbook:
        """Create playbook from YAML content."""
        try:
            data = yaml.safe_load(yaml_content)
            return cls(**data)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {e}")
        except Exception as e:
            raise ValueError(f"Failed to parse playbook: {e}")

    @classmethod
    def from_file(cls, file_path: str) -> Playbook:
        """Load playbook from YAML file."""
        with open(file_path, 'r') as f:
            return cls.from_yaml(f.read())


@dataclass
class StepResult:
    """Result from executing a playbook step."""
    step_name: str
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    retry_count: int = 0


@dataclass
class PlaybookContext:
    """Runtime context for playbook execution."""
    playbook: Playbook
    inputs: Dict[str, Any] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    step_results: List[StepResult] = field(default_factory=list)
    current_step: Optional[int] = None
    job_id: Optional[str] = None

    def get_variable(self, name: str, default: Any = None) -> Any:
        """Get a variable from context."""
        # Check step results first
        if name.startswith("steps."):
            parts = name.split(".", 2)
            if len(parts) >= 2:
                try:
                    step_index = int(parts[1])
                    if 0 <= step_index < len(self.step_results):
                        result = self.step_results[step_index]
                        if len(parts) == 3:
                            # Access nested property
                            return self._get_nested(result, parts[2], default)
                        return result
                except (ValueError, IndexError):
                    pass

        # Check variables
        if name in self.variables:
            return self.variables[name]

        # Check inputs
        if name.startswith("input."):
            input_name = name[6:]
            return self.inputs.get(input_name, default)

        return default

    def _get_nested(self, obj: Any, path: str, default: Any = None) -> Any:
        """Get nested attribute/dict value."""
        parts = path.split(".")
        current = obj

        for part in parts:
            if hasattr(current, part):
                current = getattr(current, part)
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default

        return current

    def set_variable(self, name: str, value: Any) -> None:
        """Set a variable in context."""
        self.variables[name] = value

    def add_step_result(self, result: StepResult) -> None:
        """Add a step result to context."""
        self.step_results.append(result)

    def render_template(self, template: str) -> str:
        """
        Render a Jinja2 template with context variables.

        Available in templates:
        - {{ input.* }} - Input parameters
        - {{ steps[N].result.* }} - Results from previous steps
        - {{ variables.* }} - Runtime variables
        """
        try:
            from jinja2 import Template, DebugUndefined

            # Build template context
            template_context = {
                "input": self.inputs,
                "inputs": self.inputs,  # Alias
                "variables": self.variables,
                "vars": self.variables,  # Alias
                "steps": [
                    {
                        "name": r.step_name,
                        "success": r.success,
                        "result": r.result or {},
                        "error": r.error,
                        "duration": r.duration_seconds
                    }
                    for r in self.step_results
                ]
            }

            # Add current step index
            if self.current_step is not None:
                template_context["current_step"] = self.current_step

            # Render template
            tmpl = Template(template, undefined=DebugUndefined)
            return tmpl.render(**template_context)

        except ImportError:
            logger.warning("Jinja2 not installed, returning template as-is")
            return template
        except Exception as e:
            logger.error(f"Template rendering error: {e}")
            return template

    def evaluate_condition(self, condition: str) -> bool:
        """
        Evaluate a condition expression.

        Returns True if condition passes, False otherwise.
        """
        if not condition:
            return True

        try:
            rendered = self.render_template(f"{{{{ {condition} }}}}")
            # Jinja2 returns string 'True' or 'False'
            return rendered.lower() == "true"
        except Exception as e:
            logger.error(f"Condition evaluation error: {e}")
            return False


def validate_playbook(playbook_path: str) -> bool:
    """
    Validate a playbook file.

    Args:
        playbook_path: Path to playbook YAML file

    Returns:
        True if valid, False otherwise
    """
    try:
        playbook = Playbook.from_file(playbook_path)
        logger.info(f"Playbook '{playbook.name}' is valid with {len(playbook.steps)} steps")
        return True
    except Exception as e:
        logger.error(f"Playbook validation failed: {e}")
        return False


__all__ = [
    "Playbook",
    "PlaybookStep",
    "StepRetry",
    "StepLock",
    "RetryStrategy",
    "StepResult",
    "PlaybookContext",
    "validate_playbook",
]