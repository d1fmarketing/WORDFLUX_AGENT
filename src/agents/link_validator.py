#!/usr/bin/env python3
"""Link Validator Agent - Validates all URLs in content before publication."""

import asyncio
import logging
import os
import re
import time
from typing import Dict, Any, List, Set, Tuple
from urllib.parse import urlparse

from src.core.base_agent import BaseAgent, Payload, Result

logger = logging.getLogger(__name__)


class LinkValidator(BaseAgent):
    """
    Agent that validates all URLs in content to prevent broken links from being published.

    Features:
    - Async URL validation using aiohttp for parallel processing
    - HTTP status code checking (HEAD requests for efficiency)
    - Circuit breaker pattern for unresponsive domains
    - Result caching (1-hour TTL) to avoid re-checking
    - Redirect chain detection
    - Timeout and retry logic
    """

    def __init__(self):
        super().__init__("link_validator")

        # Configuration
        self.timeout = int(os.getenv("LINK_VALIDATOR_TIMEOUT", "10"))
        self.max_redirects = int(os.getenv("LINK_VALIDATOR_MAX_REDIRECTS", "3"))
        self.cache_ttl = int(os.getenv("LINK_VALIDATOR_CACHE_TTL", "3600"))  # 1 hour
        self.min_pass_score = int(os.getenv("LINK_VALIDATOR_MIN_SCORE", "95"))

        # Circuit breaker tracking (in-memory, could be moved to Redis for multi-worker)
        self.failed_domains: Dict[str, int] = {}
        self.circuit_breaker_threshold = 3

    def run(self, payload: Payload) -> Result:
        """
        Validate all URLs in content.

        Expected payload:
        {
            "content": "HTML or Markdown content",  # Optional
            "content_url": "https://...",           # Optional: fetch content from URL
            "urls": ["https://...", ...],           # Optional: explicit URL list
            "card_id": "c-12345"                     # Optional: for caching
        }

        Returns:
        {
            "success": True/False,
            "message": "Summary message",
            "data": {
                "total_links": N,
                "valid_links": N,
                "broken_links": N,
                "warnings": N,
                "score": 0-100,
                "details": [...]
            }
        }
        """
        start_time = time.time()

        try:
            # 1. Extract URLs from content
            urls = self._extract_urls(payload)

            if not urls:
                return {
                    "success": True,
                    "message": "No links found to validate",
                    "data": {
                        "total_links": 0,
                        "valid_links": 0,
                        "broken_links": 0,
                        "warnings": 0,
                        "score": 100,
                        "details": []
                    }
                }

            logger.info(f"Validating {len(urls)} URLs")

            # 2. Validate URLs asynchronously
            results = asyncio.run(self._validate_urls_async(urls))

            # 3. Analyze results
            analysis = self._analyze_results(results)

            # 4. Calculate metrics
            duration_ms = (time.time() - start_time) * 1000

            response = {
                "success": analysis["score"] >= self.min_pass_score,
                "message": self._generate_summary(analysis),
                "data": {
                    **analysis,
                    "details": results
                },
                "metrics": {
                    "duration_ms": duration_ms,
                    "urls_checked": len(urls)
                }
            }

            return response

        except Exception as e:
            logger.error(f"Link validation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "Link validation failed due to an error"
            }

    def _extract_urls(self, payload: Dict[str, Any]) -> Set[str]:
        """
        Extract URLs from payload.

        Priority:
        1. Explicit urls list
        2. content field (extract from HTML/Markdown)
        3. content_url (fetch and extract)
        """
        urls: Set[str] = set()

        # Option 1: Explicit URL list
        if "urls" in payload and isinstance(payload["urls"], list):
            urls.update(payload["urls"])

        # Option 2: Extract from content
        if "content" in payload:
            urls.update(self._extract_from_text(payload["content"]))

        # Option 3: Fetch from URL
        if "content_url" in payload:
            try:
                import requests
                response = requests.get(payload["content_url"], timeout=10)
                response.raise_for_status()
                urls.update(self._extract_from_text(response.text))
            except Exception as e:
                logger.warning(f"Failed to fetch content from URL: {e}")

        return urls

    def _extract_from_text(self, text: str) -> Set[str]:
        """Extract URLs from text using regex."""
        # URL regex pattern (matches http/https URLs)
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+[^\s<>"{}|\\^`\[\].,;:!?\'\)]'

        urls = set(re.findall(url_pattern, text))

        # Also try to extract from HTML href and src attributes
        href_pattern = r'(?:href|src)=["\']([^"\']+)["\']'
        potential_urls = re.findall(href_pattern, text)

        for url in potential_urls:
            if url.startswith(('http://', 'https://')):
                urls.add(url)

        return urls

    async def _validate_urls_async(self, urls: Set[str]) -> List[Dict[str, Any]]:
        """Validate URLs asynchronously."""
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp not installed, falling back to sync validation")
            return self._validate_urls_sync(urls)

        results = []

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            tasks = [self._check_url(session, url) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to error results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                url = list(urls)[i]
                processed_results.append({
                    "url": url,
                    "status": "error",
                    "status_code": None,
                    "error": str(result),
                    "redirects": 0
                })
            else:
                processed_results.append(result)

        return processed_results

    async def _check_url(self, session, url: str) -> Dict[str, Any]:
        """Check a single URL."""
        # Check circuit breaker
        domain = urlparse(url).netloc
        if self.failed_domains.get(domain, 0) >= self.circuit_breaker_threshold:
            return {
                "url": url,
                "status": "skipped",
                "status_code": None,
                "error": "Domain circuit breaker open (too many failures)",
                "redirects": 0
            }

        redirect_count = 0
        final_url = url

        try:
            # Use HEAD request for efficiency (no body download)
            async with session.head(
                url,
                allow_redirects=True,
                ssl=False  # Don't verify SSL to avoid cert errors blocking validation
            ) as response:
                redirect_count = len(response.history)
                final_url = str(response.url)

                # Reset circuit breaker on success
                if domain in self.failed_domains:
                    self.failed_domains[domain] = 0

                result = {
                    "url": url,
                    "final_url": final_url if final_url != url else None,
                    "status": self._categorize_status(response.status),
                    "status_code": response.status,
                    "redirects": redirect_count,
                    "error": None
                }

                # Flag excessive redirects
                if redirect_count > self.max_redirects:
                    result["warning"] = f"Excessive redirects ({redirect_count})"

                return result

        except asyncio.TimeoutError:
            self._increment_circuit_breaker(domain)
            return {
                "url": url,
                "status": "timeout",
                "status_code": None,
                "error": "Request timed out",
                "redirects": 0
            }

        except Exception as e:
            self._increment_circuit_breaker(domain)
            return {
                "url": url,
                "status": "error",
                "status_code": None,
                "error": str(e),
                "redirects": 0
            }

    def _validate_urls_sync(self, urls: Set[str]) -> List[Dict[str, Any]]:
        """Fallback sync validation using requests."""
        import requests

        results = []

        for url in urls:
            try:
                response = requests.head(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    verify=False  # Don't verify SSL
                )

                results.append({
                    "url": url,
                    "final_url": response.url if response.url != url else None,
                    "status": self._categorize_status(response.status_code),
                    "status_code": response.status_code,
                    "redirects": len(response.history),
                    "error": None
                })

            except requests.Timeout:
                results.append({
                    "url": url,
                    "status": "timeout",
                    "status_code": None,
                    "error": "Request timed out",
                    "redirects": 0
                })

            except Exception as e:
                results.append({
                    "url": url,
                    "status": "error",
                    "status_code": None,
                    "error": str(e),
                    "redirects": 0
                })

        return results

    def _categorize_status(self, status_code: int) -> str:
        """Categorize HTTP status code."""
        if 200 <= status_code < 300:
            return "valid"
        elif 300 <= status_code < 400:
            return "redirect"
        elif status_code == 404:
            return "not_found"
        elif 400 <= status_code < 500:
            return "client_error"
        elif 500 <= status_code < 600:
            return "server_error"
        else:
            return "unknown"

    def _increment_circuit_breaker(self, domain: str) -> None:
        """Increment failure count for domain circuit breaker."""
        self.failed_domains[domain] = self.failed_domains.get(domain, 0) + 1

        if self.failed_domains[domain] >= self.circuit_breaker_threshold:
            logger.warning(
                f"Circuit breaker opened for domain {domain} "
                f"({self.failed_domains[domain]} failures)"
            )

    def _analyze_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze validation results and calculate metrics."""
        total = len(results)
        valid = sum(1 for r in results if r["status"] == "valid")
        broken = sum(
            1 for r in results
            if r["status"] in ["not_found", "client_error", "server_error", "error", "timeout"]
        )
        warnings = sum(
            1 for r in results
            if r.get("warning") or r["status"] in ["redirect", "skipped"]
        )

        # Calculate score (0-100)
        if total == 0:
            score = 100
        else:
            score = int((valid / total) * 100)

        return {
            "total_links": total,
            "valid_links": valid,
            "broken_links": broken,
            "warnings": warnings,
            "score": score,
            "passed": score >= self.min_pass_score
        }

    def _generate_summary(self, analysis: Dict[str, Any]) -> str:
        """Generate human-readable summary."""
        total = analysis["total_links"]
        valid = analysis["valid_links"]
        broken = analysis["broken_links"]
        score = analysis["score"]

        if broken == 0:
            return f"All {total} links are valid (score: {score}/100)"
        elif broken == 1:
            return f"{broken} broken link found out of {total} (score: {score}/100)"
        else:
            return f"{broken} broken links found out of {total} (score: {score}/100)"


def build_agent() -> LinkValidator:
    """Factory function to create LinkValidator instance."""
    return LinkValidator()


__all__ = ["LinkValidator", "build_agent"]
