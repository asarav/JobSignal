"""Requirements are section-aware; mentions are not hard requirements."""

from __future__ import annotations

from app.domain.services.job_requirements import analyze_job, plain_description

DESCRIPTION = """Requirements:
- Python and PostgreSQL
- Five years of Go
Nice to have:
- Docker
Responsibilities:
- Build React applications
"""


def test_required_preferred_and_mentioned_are_separate() -> None:
    analysis = analyze_job("Backend Engineer", DESCRIPTION)
    assert set(analysis.required_skills) == {"python", "postgres", "go"}
    assert analysis.preferred_skills == ("docker",)
    assert analysis.mentioned_skills == ("react",)


def test_ats_keywords_keep_importance_frequency_and_wording() -> None:
    analysis = analyze_job("Backend Engineer", DESCRIPTION)
    by_id = {item.keyword_id: item for item in analysis.ats_keywords}
    assert by_id["python"].importance == "required"
    assert by_id["python"].phrase.lower() == "python"
    assert by_id["docker"].importance == "preferred"
    assert by_id["react"].importance == "mentioned"
    assert by_id["docker"].section == "preferred"


def test_a_mention_is_never_promoted_to_required_without_a_cue() -> None:
    analysis = analyze_job(
        "Backend Engineer",
        "You will build services in Python and deploy them with Docker.",
    )
    assert analysis.required_skills == ()
    assert analysis.preferred_skills == ()
    assert set(analysis.mentioned_skills) == {"python", "docker"}


def test_inline_cues_mark_only_the_cued_line() -> None:
    analysis = analyze_job(
        "Platform Engineer",
        "Requirements: Python, SQL\n\nResponsibilities:\n- We also use React on the dashboard.",
    )
    assert set(analysis.required_skills) == {"python", "sql"}
    assert set(analysis.mentioned_skills) == {"react"}


def test_escaped_html_keeps_sections_and_go_to_market_is_not_go() -> None:
    description = (
        "&lt;h2&gt;About Vercel&lt;/h2&gt;&lt;p&gt;We build with Next.js and AI.&lt;/p&gt;"
        "&lt;h2&gt;What you’ll do&lt;/h2&gt;"
        "&lt;ul&gt;&lt;li&gt;Sell the platform.&lt;/li&gt;&lt;/ul&gt;"
        "&lt;h2&gt;What you bring&lt;/h2&gt;&lt;ul&gt;&lt;li&gt;Background as a technical "
        "go-to-market professional with knowledge of modern web "
        "infrastructure.&lt;/li&gt;&lt;/ul&gt;"
    )
    analysis = analyze_job("Account Executive", description)
    assert analysis.required_skills == ()
    assert "go" not in analysis.mentioned_skills
    assert set(analysis.sections) == {"other", "required"}


def test_plain_description_strips_stored_html() -> None:
    text = plain_description(
        "&lt;p&gt;Build APIs.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Own SQL.&lt;/li&gt;&lt;/ul&gt;"
    )
    assert text == "Build APIs.\nOwn SQL."
