#!/usr/bin/env python3
"""SEO Analyzer Agent - Validates content for SEO best practices before publication."""

import logging
import os
import re
import time
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter

from src.core.base_agent import BaseAgent, Payload, Result

logger = logging.getLogger(__name__)


class SEOAnalyzer(BaseAgent):
    """
    Agent that analyzes content for SEO compliance and best practices.

    Checks:
    - Meta title length (50-60 characters recommended)
    - Meta description length (150-160 characters recommended)
    - Keyword density and placement
    - Header hierarchy (H1-H6 structure)
    - Alt text presence on images
    - Readability metrics
    - Internal/external link balance
    """

    def __init__(self):
        super().__init__("seo_analyzer")

        # Configuration
        self.min_title_length = int(os.getenv("SEO_MIN_TITLE_LENGTH", "30"))
        self.max_title_length = int(os.getenv("SEO_MAX_TITLE_LENGTH", "60"))
        self.min_desc_length = int(os.getenv("SEO_MIN_DESC_LENGTH", "120"))
        self.max_desc_length = int(os.getenv("SEO_MAX_DESC_LENGTH", "160"))
        self.min_score = int(os.getenv("SEO_MIN_SCORE", "70"))
        self.target_keyword_density = float(os.getenv("SEO_TARGET_KEYWORD_DENSITY", "1.5"))

    def run(self, payload: Payload) -> Result:
        """
        Analyze content for SEO best practices.

        Expected payload:
        {
            "content": "HTML content",          # Required: HTML to analyze
            "title": "Page title",              # Optional: meta title
            "description": "Meta description",  # Optional: meta description
            "target_keyword": "keyword",        # Optional: target keyword for density analysis
            "card_id": "c-12345"                 # Optional: for reference
        }

        Returns:
        {
            "success": True/False,
            "message": "Summary message",
            "data": {
                "score": 0-100,
                "passed": True/False,
                "issues": {...},
                "recommendations": [...]
            }
        }
        """
        start_time = time.time()

        try:
            # 1. Extract and validate inputs
            content = payload.get("content", "")
            if not content:
                raise ValueError("content is required")

            title = payload.get("title", "")
            description = payload.get("description", "")
            target_keyword = payload.get("target_keyword", "").lower()

            # 2. Parse HTML content
            parsed = self._parse_html(content)

            # 3. Run all SEO checks
            checks = {
                "title": self._check_title(title),
                "description": self._check_description(description),
                "headers": self._check_headers(parsed["headers"]),
                "images": self._check_images(parsed["images"]),
                "keyword": self._check_keyword_density(
                    parsed["text"],
                    title,
                    parsed["headers"],
                    target_keyword
                ) if target_keyword else {"status": "skipped", "message": "No target keyword provided"},
                "links": self._check_links(parsed["links"]),
                "readability": self._check_readability(parsed["text"])
            }

            # 4. Calculate overall score
            score, passed = self._calculate_score(checks)

            # 5. Generate recommendations
            recommendations = self._generate_recommendations(checks, target_keyword)

            # 6. Build response
            duration_ms = (time.time() - start_time) * 1000

            response = {
                "success": passed,
                "message": self._generate_summary(score, passed, checks),
                "data": {
                    "score": score,
                    "passed": passed,
                    "checks": checks,
                    "recommendations": recommendations,
                    "statistics": {
                        "word_count": parsed["word_count"],
                        "paragraph_count": parsed["paragraph_count"],
                        "heading_count": len(parsed["headers"]),
                        "image_count": len(parsed["images"]),
                        "link_count": len(parsed["links"])
                    }
                },
                "metrics": {
                    "duration_ms": duration_ms
                }
            }

            return response

        except Exception as e:
            logger.error(f"SEO analysis failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "message": "SEO analysis failed due to an error"
            }

    def _parse_html(self, content: str) -> Dict[str, Any]:
        """Parse HTML content to extract SEO-relevant elements."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')

            # Extract text
            text = soup.get_text(separator=' ', strip=True)

            # Extract headers
            headers = []
            for level in range(1, 7):
                for tag in soup.find_all(f'h{level}'):
                    headers.append({
                        "level": level,
                        "text": tag.get_text(strip=True)
                    })

            # Extract images
            images = []
            for img in soup.find_all('img'):
                images.append({
                    "src": img.get('src', ''),
                    "alt": img.get('alt', '')
                })

            # Extract links
            links = []
            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                links.append({
                    "href": href,
                    "text": link.get_text(strip=True),
                    "type": "internal" if href.startswith(('/', '#')) else "external"
                })

            # Count paragraphs
            paragraph_count = len(soup.find_all('p'))

            # Count words
            word_count = len(text.split())

            return {
                "text": text,
                "headers": headers,
                "images": images,
                "links": links,
                "paragraph_count": paragraph_count,
                "word_count": word_count,
                "soup": soup
            }

        except ImportError:
            logger.warning("beautifulsoup4 not installed, using basic parsing")
            return self._parse_html_basic(content)

    def _parse_html_basic(self, content: str) -> Dict[str, Any]:
        """Fallback basic HTML parsing without BeautifulSoup."""
        # Extract headers
        headers = []
        for level in range(1, 7):
            pattern = f'<h{level}[^>]*>(.*?)</h{level}>'
            matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
            for match in matches:
                headers.append({"level": level, "text": re.sub(r'<.*?>', '', match).strip()})

        # Extract images
        images = []
        img_pattern = r'<img[^>]+>'
        for img_tag in re.findall(img_pattern, content, re.IGNORECASE):
            src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
            alt_match = re.search(r'alt=["\']([^"\']*)["\']', img_tag)
            images.append({
                "src": src_match.group(1) if src_match else "",
                "alt": alt_match.group(1) if alt_match else ""
            })

        # Extract text (remove all HTML tags)
        text = re.sub(r'<.*?>', ' ', content)
        text = re.sub(r'\s+', ' ', text).strip()

        return {
            "text": text,
            "headers": headers,
            "images": images,
            "links": [],
            "paragraph_count": content.count('<p>'),
            "word_count": len(text.split())
        }

    def _check_title(self, title: str) -> Dict[str, Any]:
        """Check meta title length and quality."""
        length = len(title)

        if not title:
            return {
                "status": "critical",
                "message": "Meta title is missing",
                "details": {"length": 0, "min": self.min_title_length, "max": self.max_title_length}
            }

        if length < self.min_title_length:
            return {
                "status": "warning",
                "message": f"Title too short ({length} chars, recommended {self.min_title_length}-{self.max_title_length})",
                "details": {"length": length, "min": self.min_title_length, "max": self.max_title_length}
            }

        if length > self.max_title_length:
            return {
                "status": "warning",
                "message": f"Title too long ({length} chars, recommended {self.min_title_length}-{self.max_title_length})",
                "details": {"length": length, "min": self.min_title_length, "max": self.max_title_length}
            }

        return {
            "status": "pass",
            "message": f"Title length optimal ({length} chars)",
            "details": {"length": length, "min": self.min_title_length, "max": self.max_title_length}
        }

    def _check_description(self, description: str) -> Dict[str, Any]:
        """Check meta description length and quality."""
        length = len(description)

        if not description:
            return {
                "status": "critical",
                "message": "Meta description is missing",
                "details": {"length": 0, "min": self.min_desc_length, "max": self.max_desc_length}
            }

        if length < self.min_desc_length:
            return {
                "status": "warning",
                "message": f"Description too short ({length} chars, recommended {self.min_desc_length}-{self.max_desc_length})",
                "details": {"length": length, "min": self.min_desc_length, "max": self.max_desc_length}
            }

        if length > self.max_desc_length:
            return {
                "status": "warning",
                "message": f"Description too long ({length} chars, recommended {self.min_desc_length}-{self.max_desc_length})",
                "details": {"length": length, "min": self.min_desc_length, "max": self.max_desc_length}
            }

        return {
            "status": "pass",
            "message": f"Description length optimal ({length} chars)",
            "details": {"length": length, "min": self.min_desc_length, "max": self.max_desc_length}
        }

    def _check_headers(self, headers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Check header hierarchy and structure."""
        if not headers:
            return {
                "status": "critical",
                "message": "No headers found",
                "details": {"h1_count": 0, "hierarchy_valid": False}
            }

        # Count H1 tags
        h1_count = sum(1 for h in headers if h["level"] == 1)

        issues = []

        # Check H1 count
        if h1_count == 0:
            issues.append("No H1 tag found")
        elif h1_count > 1:
            issues.append(f"Multiple H1 tags found ({h1_count}), should have exactly one")

        # Check hierarchy (no skipped levels)
        levels = [h["level"] for h in headers]
        for i in range(len(levels) - 1):
            if levels[i+1] > levels[i] + 1:
                issues.append(f"Header hierarchy skip detected: H{levels[i]} → H{levels[i+1]}")

        if issues:
            return {
                "status": "warning",
                "message": f"{len(issues)} header issue(s) found",
                "details": {"h1_count": h1_count, "issues": issues}
            }

        return {
            "status": "pass",
            "message": "Header structure valid",
            "details": {"h1_count": h1_count, "total_headers": len(headers)}
        }

    def _check_images(self, images: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Check image alt text presence."""
        if not images:
            return {
                "status": "info",
                "message": "No images found",
                "details": {"total": 0, "missing_alt": 0}
            }

        missing_alt = sum(1 for img in images if not img["alt"])

        if missing_alt > 0:
            percentage = (missing_alt / len(images)) * 100
            return {
                "status": "warning",
                "message": f"{missing_alt} out of {len(images)} images missing alt text ({percentage:.0f}%)",
                "details": {"total": len(images), "missing_alt": missing_alt}
            }

        return {
            "status": "pass",
            "message": f"All {len(images)} images have alt text",
            "details": {"total": len(images), "missing_alt": 0}
        }

    def _check_keyword_density(
        self,
        text: str,
        title: str,
        headers: List[Dict[str, Any]],
        keyword: str
    ) -> Dict[str, Any]:
        """Check target keyword placement and density."""
        if not keyword:
            return {"status": "skipped", "message": "No target keyword provided"}

        text_lower = text.lower()
        title_lower = title.lower()

        # Count keyword occurrences
        keyword_count = text_lower.count(keyword)
        word_count = len(text.split())

        # Calculate density
        density = (keyword_count / word_count * 100) if word_count > 0 else 0

        # Check keyword in title
        in_title = keyword in title_lower

        # Check keyword in first paragraph (first 100 words)
        first_paragraph = ' '.join(text.split()[:100]).lower()
        in_first_paragraph = keyword in first_paragraph

        # Check keyword in headers
        header_texts = ' '.join([h["text"] for h in headers]).lower()
        in_headers = keyword in header_texts

        issues = []
        if not in_title:
            issues.append("Keyword not in title")
        if not in_first_paragraph:
            issues.append("Keyword not in first paragraph")
        if not in_headers:
            issues.append("Keyword not in any header")

        if density < 0.5:
            issues.append(f"Keyword density too low ({density:.2f}%)")
        elif density > 3.0:
            issues.append(f"Keyword density too high ({density:.2f}%, may be keyword stuffing)")

        if issues:
            return {
                "status": "warning",
                "message": f"{len(issues)} keyword issue(s) found",
                "details": {
                    "keyword": keyword,
                    "count": keyword_count,
                    "density": round(density, 2),
                    "in_title": in_title,
                    "in_first_paragraph": in_first_paragraph,
                    "in_headers": in_headers,
                    "issues": issues
                }
            }

        return {
            "status": "pass",
            "message": f"Keyword placement optimal (density: {density:.2f}%)",
            "details": {
                "keyword": keyword,
                "count": keyword_count,
                "density": round(density, 2),
                "in_title": in_title,
                "in_first_paragraph": in_first_paragraph,
                "in_headers": in_headers
            }
        }

    def _check_links(self, links: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Check internal/external link balance."""
        if not links:
            return {
                "status": "info",
                "message": "No links found",
                "details": {"total": 0, "internal": 0, "external": 0}
            }

        internal = sum(1 for link in links if link["type"] == "internal")
        external = len(links) - internal

        return {
            "status": "pass",
            "message": f"{len(links)} links ({internal} internal, {external} external)",
            "details": {"total": len(links), "internal": internal, "external": external}
        }

    def _check_readability(self, text: str) -> Dict[str, Any]:
        """Check basic readability metrics."""
        if not text:
            return {"status": "info", "message": "No text content to analyze"}

        words = text.split()
        word_count = len(words)

        if word_count < 300:
            return {
                "status": "warning",
                "message": f"Content too short ({word_count} words, minimum 300 recommended)",
                "details": {"word_count": word_count}
            }

        # Calculate average word length
        avg_word_length = sum(len(word) for word in words) / word_count if word_count > 0 else 0

        # Simple sentence count (count periods, question marks, exclamation marks)
        sentence_count = text.count('.') + text.count('?') + text.count('!')

        # Average words per sentence
        avg_words_per_sentence = word_count / sentence_count if sentence_count > 0 else 0

        issues = []
        if avg_words_per_sentence > 25:
            issues.append(f"Sentences too long (avg {avg_words_per_sentence:.0f} words)")

        if issues:
            return {
                "status": "warning",
                "message": f"{len(issues)} readability issue(s) found",
                "details": {
                    "word_count": word_count,
                    "avg_word_length": round(avg_word_length, 1),
                    "avg_words_per_sentence": round(avg_words_per_sentence, 1),
                    "issues": issues
                }
            }

        return {
            "status": "pass",
            "message": "Readability metrics acceptable",
            "details": {
                "word_count": word_count,
                "avg_word_length": round(avg_word_length, 1),
                "avg_words_per_sentence": round(avg_words_per_sentence, 1)
            }
        }

    def _calculate_score(self, checks: Dict[str, Dict[str, Any]]) -> Tuple[int, bool]:
        """Calculate overall SEO score (0-100)."""
        points = 0
        max_points = 0

        # Weight different checks
        weights = {
            "title": 15,
            "description": 15,
            "headers": 20,
            "images": 10,
            "keyword": 20,
            "links": 10,
            "readability": 10
        }

        for check_name, weight in weights.items():
            max_points += weight
            check = checks.get(check_name, {})
            status = check.get("status")

            if status == "pass":
                points += weight
            elif status == "warning":
                points += weight * 0.5
            elif status == "info":
                points += weight * 0.75
            elif status == "skipped":
                max_points -= weight  # Don't count skipped checks

        score = int((points / max_points * 100)) if max_points > 0 else 0
        passed = score >= self.min_score

        return score, passed

    def _generate_recommendations(
        self,
        checks: Dict[str, Dict[str, Any]],
        target_keyword: str
    ) -> List[str]:
        """Generate actionable SEO recommendations."""
        recommendations = []

        for check_name, check in checks.items():
            if check["status"] in ["critical", "warning"]:
                # Add specific recommendations based on check type
                if check_name == "title" and "too short" in check["message"]:
                    recommendations.append(
                        f"Expand meta title to {self.min_title_length}-{self.max_title_length} characters"
                    )
                elif check_name == "description" and "missing" in check["message"]:
                    recommendations.append(
                        f"Add meta description ({self.min_desc_length}-{self.max_desc_length} characters)"
                    )
                elif check_name == "headers" and "No H1" in str(check.get("details", {})):
                    recommendations.append("Add an H1 header to your content")
                elif check_name == "images" and check.get("details", {}).get("missing_alt", 0) > 0:
                    recommendations.append(
                        f"Add alt text to {check['details']['missing_alt']} image(s)"
                    )
                elif check_name == "keyword" and target_keyword:
                    issues = check.get("details", {}).get("issues", [])
                    for issue in issues:
                        if "not in title" in issue:
                            recommendations.append(f"Include '{target_keyword}' in your title")
                        elif "not in first paragraph" in issue:
                            recommendations.append(f"Mention '{target_keyword}' early in your content")

        return recommendations

    def _generate_summary(
        self,
        score: int,
        passed: bool,
        checks: Dict[str, Dict[str, Any]]
    ) -> str:
        """Generate human-readable summary."""
        critical_count = sum(1 for c in checks.values() if c.get("status") == "critical")
        warning_count = sum(1 for c in checks.values() if c.get("status") == "warning")

        if passed:
            if score >= 90:
                return f"Excellent SEO (score: {score}/100)"
            else:
                return f"Good SEO (score: {score}/100, {warning_count} warnings)"
        else:
            if critical_count > 0:
                return f"SEO needs improvement (score: {score}/100, {critical_count} critical, {warning_count} warnings)"
            else:
                return f"SEO below minimum (score: {score}/100, {warning_count} warnings)"


def build_agent() -> SEOAnalyzer:
    """Factory function to create SEOAnalyzer instance."""
    return SEOAnalyzer()


__all__ = ["SEOAnalyzer", "build_agent"]
