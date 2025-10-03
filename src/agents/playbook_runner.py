#!/usr/bin/env python3
"""Playbook runner agent that executes multi-step workflows."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from src.core.base_agent import BaseAgent
from src.core.job import Job
from src.core.locks import acquire_lock, LockAcquisitionError
from src.core.playbook import (
    Playbook,
    PlaybookContext,
    RetryStrategy,
    StepResult,
)
from src.core.queue import load_default_queue
from src.core.registry import create_agent

logger = logging.getLogger(__name__)


class PlaybookRunnerAgent(BaseAgent):
    """Agent that executes playbook workflows."""

    def __init__(self):
        """Initialize playbook runner."""
        super().__init__(name="playbook_runner")
        self.queue = load_default_queue()

    def run(self, payload: dict) -> dict:
        """
        Execute a playbook.

        Expected payload:
        {
            "playbook_id": "release-train",  # Preferred (via catalog)
            "playbook_path": "/path/to/playbook.yaml",  # OR
            "playbook_yaml": "name: Test\nsteps:...",   # OR
            "playbook": { ... },  # Playbook dict
            "params": {  # Or "inputs" (both supported)
                "param1": "value1",
                "param2": "value2"
            },
            "job_id": "abc123"  # Optional
        }
        """
        try:
            # Load playbook
            playbook = self._load_playbook(payload)
            if not playbook:
                return {
                    "success": False,
                    "error": "Failed to load playbook"
                }

            # Map params to inputs (for chat compatibility)
            # Support both "inputs" (playbook native) and "params" (chat API)
            inputs = payload.get("inputs") or payload.get("params", {})

            # Create execution context
            context = PlaybookContext(
                playbook=playbook,
                inputs=inputs,
                job_id=payload.get("job_id")
            )

            logger.info(f"Starting playbook '{playbook.name}' with {len(playbook.steps)} steps")

            # Execute steps
            success = True
            total_duration = 0.0

            for i, step in enumerate(playbook.steps):
                context.current_step = i

                # Check condition
                if step.condition:
                    if not context.evaluate_condition(step.condition):
                        logger.info(f"Skipping step '{step.name}' - condition not met")
                        context.add_step_result(StepResult(
                            step_name=step.name,
                            success=True,
                            result={"skipped": True}
                        ))
                        continue

                # Execute step
                logger.info(f"Executing step {i+1}/{len(playbook.steps)}: {step.name}")
                step_result = self._execute_step(step, context)
                context.add_step_result(step_result)
                total_duration += step_result.duration_seconds

                if not step_result.success:
                    logger.error(f"Step '{step.name}' failed: {step_result.error}")
                    if not step.continue_on_error:
                        success = False
                        break
                    logger.warning(f"Continuing despite error in step '{step.name}'")

            # Build final result
            result = {
                "success": success,
                "playbook": playbook.name,
                "steps_executed": len(context.step_results),
                "total_duration": total_duration,
                "step_results": [
                    {
                        "name": r.step_name,
                        "success": r.success,
                        "duration": r.duration_seconds,
                        "retry_count": r.retry_count,
                        "error": r.error,
                        "result": r.result
                    }
                    for r in context.step_results
                ]
            }

            if success:
                logger.info(f"Playbook '{playbook.name}' completed successfully in {total_duration:.1f}s")
            else:
                logger.error(f"Playbook '{playbook.name}' failed")

            return result

        except Exception as e:
            logger.error(f"Playbook execution error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def _load_playbook(self, payload: dict) -> Optional[Playbook]:
        """Load playbook from various sources."""
        try:
            # Priority 1: Load by ID (from catalog) - PREFERRED for chat
            if "playbook_id" in payload:
                from src.core.playbook_catalog import resolve_playbook_path

                playbook_id = payload["playbook_id"]
                file_path = resolve_playbook_path(playbook_id)

                if not file_path:
                    raise ValueError(
                        f"Unknown playbook ID: '{playbook_id}'. "
                        f"Check catalog in src/core/playbook_catalog.py"
                    )

                logger.info(f"Loading playbook by ID: '{playbook_id}' → {file_path}")
                return Playbook.from_file(file_path)

            # Priority 2: Load by file path (backwards compatibility)
            elif "playbook_path" in payload:
                return Playbook.from_file(payload["playbook_path"])

            # Priority 3: Load from inline YAML
            elif "playbook_yaml" in payload:
                return Playbook.from_yaml(payload["playbook_yaml"])

            # Priority 4: Load from dict
            elif "playbook" in payload:
                return Playbook(**payload["playbook"])

            else:
                logger.error("No playbook source provided (expected: playbook_id, playbook_path, playbook_yaml, or playbook)")
                return None

        except Exception as e:
            logger.error(f"Failed to load playbook: {e}")
            return None

    def _execute_step(self, step, context: PlaybookContext) -> StepResult:
        """Execute a single playbook step with retries and locking."""
        start_time = time.time()
        retry_count = 0
        max_attempts = step.retry.max_attempts if step.retry else 1

        while retry_count < max_attempts:
            try:
                # Acquire lock if needed
                lock = None
                if step.lock:
                    try:
                        lock_timeout = None if step.lock.wait else 0
                        lock = acquire_lock(
                            resource=self._render_value(step.lock.resource, context),
                            ttl=step.lock.timeout,
                            timeout=lock_timeout
                        )
                        logger.info(f"Acquired lock for resource '{step.lock.resource}'")
                    except LockAcquisitionError as e:
                        return StepResult(
                            step_name=step.name,
                            success=False,
                            error=f"Failed to acquire lock: {e}",
                            duration_seconds=time.time() - start_time
                        )

                try:
                    # Render step payload with template variables
                    rendered_payload = self._render_payload(step.with_, context)

                    # Create and execute agent job
                    result = self._execute_agent(step.agent, rendered_payload, step.timeout_seconds)

                    if result.get("success", False):
                        return StepResult(
                            step_name=step.name,
                            success=True,
                            result=result,
                            duration_seconds=time.time() - start_time,
                            retry_count=retry_count
                        )
                    else:
                        error = result.get("error", "Unknown error")
                        if retry_count < max_attempts - 1:
                            # Calculate retry delay
                            delay = self._calculate_retry_delay(retry_count, step.retry)
                            logger.warning(f"Step '{step.name}' failed (attempt {retry_count + 1}/{max_attempts}), retrying in {delay}s")
                            time.sleep(delay)
                            retry_count += 1
                            continue
                        else:
                            return StepResult(
                                step_name=step.name,
                                success=False,
                                error=error,
                                duration_seconds=time.time() - start_time,
                                retry_count=retry_count
                            )

                finally:
                    # Release lock
                    if lock:
                        lock.__exit__(None, None, None)

            except Exception as e:
                logger.error(f"Step execution error: {e}")
                if retry_count < max_attempts - 1:
                    delay = self._calculate_retry_delay(retry_count, step.retry)
                    logger.warning(f"Step '{step.name}' error (attempt {retry_count + 1}/{max_attempts}), retrying in {delay}s")
                    time.sleep(delay)
                    retry_count += 1
                else:
                    return StepResult(
                        step_name=step.name,
                        success=False,
                        error=str(e),
                        duration_seconds=time.time() - start_time,
                        retry_count=retry_count
                    )

        # Should not reach here
        return StepResult(
            step_name=step.name,
            success=False,
            error="Max retries exceeded",
            duration_seconds=time.time() - start_time,
            retry_count=retry_count
        )

    def _execute_agent(self, agent_name: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """Execute an agent directly (synchronous for now)."""
        try:
            # Create agent instance
            agent = create_agent(agent_name)

            # Execute with timeout
            # Note: For simplicity, we're executing synchronously
            # In production, you might want to use async or subprocess with actual timeout
            result = agent.run(payload)

            # Ensure result is a dict with success flag
            if isinstance(result, dict):
                if "success" not in result:
                    # Assume success if no explicit flag
                    result["success"] = True
                return result
            else:
                return {
                    "success": True,
                    "result": result
                }

        except Exception as e:
            logger.error(f"Agent execution error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def _render_value(self, value: Any, context: PlaybookContext) -> Any:
        """Render a value with template variables."""
        if isinstance(value, str):
            return context.render_template(value)
        elif isinstance(value, dict):
            return self._render_payload(value, context)
        elif isinstance(value, list):
            return [self._render_value(item, context) for item in value]
        else:
            return value

    def _render_payload(self, payload: Dict[str, Any], context: PlaybookContext) -> Dict[str, Any]:
        """Render payload with template variables."""
        rendered = {}
        for key, value in payload.items():
            rendered[key] = self._render_value(value, context)
        return rendered

    def _calculate_retry_delay(self, retry_count: int, retry_config) -> float:
        """Calculate retry delay based on strategy."""
        if not retry_config:
            return 1.0

        base_delay = retry_config.delay_seconds

        if retry_config.backoff == RetryStrategy.EXPONENTIAL:
            return min(base_delay * (2 ** retry_count), 300)  # Max 5 minutes
        elif retry_config.backoff == RetryStrategy.LINEAR:
            return min(base_delay * (retry_count + 1), 300)
        else:
            return base_delay


def build_agent() -> PlaybookRunnerAgent:
    """Factory function to create PlaybookRunnerAgent instance."""
    return PlaybookRunnerAgent()


__all__ = ["PlaybookRunnerAgent", "build_agent"]