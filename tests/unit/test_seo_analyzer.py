#!/usr/bin/env python3
"""Unit tests for SEOAnalyzer agent."""

import pytest
from unittest.mock import Mock, patch
from src.agents.seo_analyzer import SEOAnalyzer


@pytest.fixture
def agent():
    """Create SEOAnalyzer instance."""
    return SEOAnalyzer()


@pytest.fixture
def sample_html():
    """Sample HTML content for testing."""
    return """
    <html>
    <head>
        <title>Python Programming Guide</title>
    </head>
    <body>
        <h1>Python Programming Tutorial</h1>
        <p>Learn Python programming with this comprehensive guide.</p>

        <h2>Getting Started</h2>
        <p>Python is a versatile programming language used for web development,
        data analysis, machine learning, and more.</p>

        <h2>Python Basics</h2>
        <p>Start with Python syntax and fundamental concepts.</p>

        <img src="python-logo.png" alt="Python Logo">
        <img src="code-example.png" alt="Code Example">

        <a href="/tutorial">Internal Link</a>
        <a href="https://python.org">External Link</a>
    </body>
    </html>
    """


@pytest.fixture
def sample_html_no_h1():
    """Sample HTML without H1 tag."""
    return """
    <html>
    <body>
        <h2>Section Title</h2>
        <p>Content without H1 tag.</p>
    </body>
    </html>
    """


def test_check_title_optimal_length(agent):
    """Test title check with optimal length."""
    title = "Python Programming Guide - Learn Python Basics"  # 46 chars

    result = agent._check_title(title)

    assert result["status"] == "pass"
    assert result["details"]["length"] == len(title)


def test_check_title_missing(agent):
    """Test title check with missing title."""
    result = agent._check_title("")

    assert result["status"] == "critical"
    assert "missing" in result["message"].lower()


def test_check_title_too_short(agent):
    """Test title check with title too short."""
    result = agent._check_title("Short")

    assert result["status"] == "warning"
    assert "too short" in result["message"].lower()


def test_check_title_too_long(agent):
    """Test title check with title too long."""
    long_title = "This is an extremely long title that exceeds the recommended sixty character limit for SEO optimization"

    result = agent._check_title(long_title)

    assert result["status"] == "warning"
    assert "too long" in result["message"].lower()


def test_check_description_optimal(agent):
    """Test description check with optimal length."""
    description = (
        "Learn Python programming with our comprehensive tutorial. "
        "Covers syntax, data structures, functions, and best practices. "
        "Perfect for beginners and intermediate developers."
    )  # ~150 chars

    result = agent._check_description(description)

    assert result["status"] == "pass"


def test_check_description_missing(agent):
    """Test description check with missing description."""
    result = agent._check_description("")

    assert result["status"] == "critical"
    assert "missing" in result["message"].lower()


def test_check_headers_valid_structure(agent):
    """Test header check with valid structure."""
    headers = [
        {"level": 1, "text": "Main Title"},
        {"level": 2, "text": "Section 1"},
        {"level": 3, "text": "Subsection 1.1"},
        {"level": 2, "text": "Section 2"}
    ]

    result = agent._check_headers(headers)

    assert result["status"] == "pass"
    assert result["details"]["h1_count"] == 1


def test_check_headers_no_h1(agent):
    """Test header check with no H1 tag."""
    headers = [
        {"level": 2, "text": "Section 1"},
        {"level": 3, "text": "Subsection 1.1"}
    ]

    result = agent._check_headers(headers)

    assert result["status"] == "warning"
    assert "No H1" in str(result["details"])


def test_check_headers_multiple_h1(agent):
    """Test header check with multiple H1 tags."""
    headers = [
        {"level": 1, "text": "Title 1"},
        {"level": 1, "text": "Title 2"},
        {"level": 2, "text": "Section 1"}
    ]

    result = agent._check_headers(headers)

    assert result["status"] == "warning"
    assert result["details"]["h1_count"] == 2


def test_check_headers_hierarchy_skip(agent):
    """Test header check with hierarchy skip."""
    headers = [
        {"level": 1, "text": "Main Title"},
        {"level": 3, "text": "Subsection"}  # Skips H2
    ]

    result = agent._check_headers(headers)

    assert result["status"] == "warning"
    assert "hierarchy skip" in str(result["details"]).lower()


def test_check_headers_no_headers(agent):
    """Test header check with no headers."""
    result = agent._check_headers([])

    assert result["status"] == "critical"
    assert "No headers" in result["message"]


def test_check_images_all_have_alt(agent):
    """Test image check with all images having alt text."""
    images = [
        {"src": "image1.png", "alt": "Description 1"},
        {"src": "image2.png", "alt": "Description 2"}
    ]

    result = agent._check_images(images)

    assert result["status"] == "pass"
    assert result["details"]["missing_alt"] == 0


def test_check_images_missing_alt(agent):
    """Test image check with missing alt text."""
    images = [
        {"src": "image1.png", "alt": "Description 1"},
        {"src": "image2.png", "alt": ""},
        {"src": "image3.png", "alt": ""}
    ]

    result = agent._check_images(images)

    assert result["status"] == "warning"
    assert result["details"]["missing_alt"] == 2


def test_check_images_no_images(agent):
    """Test image check with no images."""
    result = agent._check_images([])

    assert result["status"] == "info"
    assert "No images" in result["message"]


def test_check_keyword_density_optimal(agent):
    """Test keyword density check with optimal placement."""
    text = "Python is great. Learn Python programming. Python makes coding easy. " * 20
    title = "Learn Python Programming"
    headers = [{"level": 1, "text": "Python Tutorial"}]
    keyword = "python"

    result = agent._check_keyword_density(text, title, headers, keyword)

    assert result["status"] in ["pass", "warning"]
    assert result["details"]["in_title"] is True
    assert result["details"]["in_headers"] is True


def test_check_keyword_density_not_in_title(agent):
    """Test keyword density when keyword missing from title."""
    text = "Python is great. " * 20
    title = "Programming Tutorial"
    headers = []
    keyword = "python"

    result = agent._check_keyword_density(text, title, headers, keyword)

    assert result["status"] == "warning"
    assert result["details"]["in_title"] is False
    assert "not in title" in str(result["details"]["issues"])


def test_check_keyword_density_too_low(agent):
    """Test keyword density when too low."""
    text = "This is a long article without the target term mentioned enough. " * 50
    title = "Article Title"
    headers = []
    keyword = "python"

    result = agent._check_keyword_density(text, title, headers, keyword)

    assert result["status"] == "warning"
    assert "too low" in str(result["details"].get("issues", []))


def test_check_keyword_density_no_keyword(agent):
    """Test keyword density with no keyword provided."""
    result = agent._check_keyword_density("text", "title", [], "")

    assert result["status"] == "skipped"


def test_check_links_balanced(agent):
    """Test link check with balanced links."""
    links = [
        {"href": "/page1", "type": "internal"},
        {"href": "/page2", "type": "internal"},
        {"href": "https://external.com", "type": "external"}
    ]

    result = agent._check_links(links)

    assert result["status"] == "pass"
    assert result["details"]["internal"] == 2
    assert result["details"]["external"] == 1


def test_check_links_no_links(agent):
    """Test link check with no links."""
    result = agent._check_links([])

    assert result["status"] == "info"
    assert "No links" in result["message"]


def test_check_readability_optimal(agent):
    """Test readability check with optimal metrics."""
    text = "This is a sentence. This is another sentence. " * 100  # ~500 words

    result = agent._check_readability(text)

    assert result["status"] in ["pass", "warning"]
    assert result["details"]["word_count"] > 300


def test_check_readability_too_short(agent):
    """Test readability with content too short."""
    text = "Short content."

    result = agent._check_readability(text)

    assert result["status"] == "warning"
    assert "too short" in result["message"].lower()


def test_check_readability_no_content(agent):
    """Test readability with no content."""
    result = agent._check_readability("")

    assert result["status"] == "info"


def test_parse_html_with_beautifulsoup(agent, sample_html):
    """Test HTML parsing with BeautifulSoup."""
    parsed = agent._parse_html(sample_html)

    assert "text" in parsed
    assert "headers" in parsed
    assert "images" in parsed
    assert "links" in parsed
    assert parsed["word_count"] > 0
    assert len(parsed["headers"]) >= 3
    assert len(parsed["images"]) == 2


def test_parse_html_basic_fallback(agent, sample_html):
    """Test HTML parsing with basic fallback."""
    with patch('src.agents.seo_analyzer.BeautifulSoup', side_effect=ImportError):
        parsed = agent._parse_html_basic(sample_html)

        assert "text" in parsed
        assert "headers" in parsed
        assert len(parsed["headers"]) >= 1


def test_calculate_score_all_pass(agent):
    """Test score calculation with all checks passing."""
    checks = {
        "title": {"status": "pass"},
        "description": {"status": "pass"},
        "headers": {"status": "pass"},
        "images": {"status": "pass"},
        "keyword": {"status": "pass"},
        "links": {"status": "pass"},
        "readability": {"status": "pass"}
    }

    score, passed = agent._calculate_score(checks)

    assert score == 100
    assert passed is True


def test_calculate_score_with_warnings(agent):
    """Test score calculation with warnings."""
    checks = {
        "title": {"status": "pass"},
        "description": {"status": "warning"},
        "headers": {"status": "warning"},
        "images": {"status": "pass"},
        "keyword": {"status": "warning"},
        "links": {"status": "pass"},
        "readability": {"status": "pass"}
    }

    score, passed = agent._calculate_score(checks)

    assert score < 100
    assert score > 50


def test_calculate_score_with_critical(agent):
    """Test score calculation with critical issues."""
    checks = {
        "title": {"status": "critical"},
        "description": {"status": "critical"},
        "headers": {"status": "warning"},
        "images": {"status": "pass"},
        "keyword": {"status": "skipped"},
        "links": {"status": "pass"},
        "readability": {"status": "pass"}
    }

    score, passed = agent._calculate_score(checks)

    assert score < agent.min_score
    assert passed is False


def test_generate_recommendations_with_issues(agent):
    """Test recommendation generation with issues."""
    checks = {
        "title": {"status": "warning", "message": "Title too short"},
        "description": {"status": "critical", "message": "Meta description is missing"},
        "headers": {"status": "warning", "details": {"issues": ["No H1 tag found"]}},
        "images": {"status": "warning", "details": {"missing_alt": 3}},
        "keyword": {"status": "warning", "details": {"issues": ["Keyword not in title"]}}
    }

    recommendations = agent._generate_recommendations(checks, "python")

    assert len(recommendations) > 0
    assert any("title" in r.lower() for r in recommendations)
    assert any("description" in r.lower() for r in recommendations)


def test_generate_recommendations_no_issues(agent):
    """Test recommendation generation with no issues."""
    checks = {
        "title": {"status": "pass"},
        "description": {"status": "pass"},
        "headers": {"status": "pass"},
        "images": {"status": "pass"},
        "keyword": {"status": "pass"},
        "links": {"status": "pass"},
        "readability": {"status": "pass"}
    }

    recommendations = agent._generate_recommendations(checks, "python")

    assert len(recommendations) == 0


def test_generate_summary_excellent_seo(agent):
    """Test summary generation for excellent SEO."""
    summary = agent._generate_summary(95, True, {})

    assert "Excellent" in summary or "score: 95" in summary


def test_generate_summary_needs_improvement(agent):
    """Test summary generation for SEO needing improvement."""
    checks = {
        "title": {"status": "critical"},
        "description": {"status": "warning"}
    }

    summary = agent._generate_summary(50, False, checks)

    assert "needs improvement" in summary.lower() or "below minimum" in summary.lower()


def test_integration_full_analysis(agent, sample_html):
    """Test full SEO analysis integration."""
    payload = {
        "content": sample_html,
        "title": "Python Programming Guide - Learn Python Basics",
        "description": "Comprehensive Python programming tutorial for beginners. Learn syntax, data structures, and best practices. Start coding today!",
        "target_keyword": "python"
    }

    result = agent.run(payload)

    assert "success" in result
    assert "data" in result
    assert "score" in result["data"]
    assert result["data"]["score"] >= 0
    assert result["data"]["score"] <= 100
    assert "recommendations" in result["data"]


def test_integration_missing_content(agent):
    """Test integration with missing content."""
    payload = {}

    result = agent.run(payload)

    assert result["success"] is False
    assert "error" in result


def test_integration_minimal_content(agent):
    """Test integration with minimal content."""
    payload = {
        "content": "<p>Minimal content</p>",
        "title": "Short Title",
        "description": "Short description"
    }

    result = agent.run(payload)

    assert "data" in result
    assert result["data"]["score"] < 100  # Should have warnings


def test_error_handling_invalid_payload(agent):
    """Test error handling with invalid payload."""
    payload = {"content": None}

    result = agent.run(payload)

    assert result["success"] is False
    assert "error" in result
