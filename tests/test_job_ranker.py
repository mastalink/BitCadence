"""Tests for BitCadence Jev-ranked job board and hard filters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from typer.testing import CliRunner

import mco.cli as cli
from mco.jobs.filters import (
    MIN_FIXED_BUDGET,
    MIN_HOURLY_BUDGET,
    REASON_EXCLUDE_ACADEMIC_DISHONESTY,
    REASON_EXCLUDE_ADULT_GAMBLING,
    REASON_EXCLUDE_CREDENTIAL_TAKEOVER,
    REASON_EXCLUDE_FINANCIAL_CONFLICT,
    REASON_EXCLUDE_LOGIN_SCRAPING,
    REASON_EXCLUDE_PHYSICAL_HARDWARE,
    REASON_EXCLUDE_SPAM_FAKE_REVIEWS,
    REASON_FLAG_LOW_BUDGET,
    HardFilterEngine,
)
from mco.jobs.importers import import_from_csv, import_from_json, load_postings_from_file
from mco.jobs.models import ClientInfo, JobPosting, RankedJob
from mco.jobs.ranker import (
    RUBRIC_SCORE_MAX,
    WEIGHT_CLARITY,
    WEIGHT_CLIENT_QUALITY,
    WEIGHT_COMPETITION,
    WEIGHT_DELIVERY_RISK,
    WEIGHT_FLEET_FIT,
    WEIGHT_VALUE,
    JobRanker,
    compute_composite_rank,
    deterministic_heuristic_scores,
    normalize_rubric_score,
)
from mco.jobs.upwork import (
    UpworkAuthenticationError,
    UpworkError,
    UpworkGraphQLAdapter,
)
from mco.orchestrator.jev import (
    DecisionReceipt,
    JevConfig,
    JevProvider,
    _validate_questions,
    get_question_set,
)
from mco.orchestrator.jev_questions import (
    SERVICE_JOB_FIT,
    SERVICE_JOB_FIT_VERSION,
    get_registry,
    registry_digest,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── 1. Question Set Validation ───────────────────────────────────────────────

def test_question_set_validates():
    """service_job_fit v1.0.0 must satisfy all wire schema & rubric list rules."""
    registry = get_registry(SERVICE_JOB_FIT)
    assert registry["use_case_id"] == SERVICE_JOB_FIT
    assert registry["version"] == SERVICE_JOB_FIT_VERSION

    questions = registry["questions"]
    # Must pass wire schema validation (ordered rubric lists of >= 2 items)
    _validate_questions(questions)

    expected_questions = {
        "fleet_fit",
        "value",
        "clarity",
        "client_quality",
        "competition",
        "delivery_risk",
    }
    assert set(questions.keys()) == expected_questions

    for qname, qdata in questions.items():
        assert qdata["type"] == "score"
        assert isinstance(qdata["instructions"], str) and len(qdata["instructions"]) > 10
        criteria = qdata["criteria"]
        assert isinstance(criteria, list)
        assert len(criteria) >= 2, f"{qname} rubric must have at least 2 levels"
        for level in criteria:
            assert isinstance(level, str) and len(level) > 5

    # Digest is reproducible 64-char sha256
    d1 = registry_digest(SERVICE_JOB_FIT)
    d2 = registry_digest(SERVICE_JOB_FIT)
    assert d1 == d2
    assert len(d1) == 64

    # Also registered in jev.py QUESTION_SET_REGISTRY
    qset_from_jev = get_question_set(SERVICE_JOB_FIT, SERVICE_JOB_FIT_VERSION)
    assert set(qset_from_jev.keys()) == expected_questions
    _validate_questions(qset_from_jev)


# ── 2. Hard Filters Exclude Bank Client and Login-Scrape Regardless of Jev ───

def test_hard_filters_exclude_bank_client_and_login_scrape_regardless_of_jev():
    """Hard filters exclude financial conflict and login-scrape jobs regardless of Jev."""
    bank_job = JobPosting(
        source="upwork",
        id="bank-1",
        title="Python Microservice Developer for Retail Banking",
        description="Build microservices connecting to First National Bank core account ledger.",
        budget_type="fixed",
        budget_min=1500.0,
        budget_max=1500.0,
        skills=["Python", "FastAPI"],
        client=ClientInfo(country="United States", payment_verified=True, rating=5.0),
    )

    scrape_job = JobPosting(
        source="upwork",
        id="scrape-1",
        title="Scrape member profiles behind login bypassing Cloudflare",
        description="We need a bot to log in with session cookies and scrape data behind captcha challenges.",
        budget_type="fixed",
        budget_min=800.0,
        budget_max=800.0,
        skills=["Python", "Selenium"],
        client=ClientInfo(country="United States", payment_verified=True, rating=4.9),
    )

    res_bank = HardFilterEngine.evaluate(bank_job)
    assert res_bank.eligible is False
    assert any("bank" in r.lower() or "federal reserve" in r.lower() for r in res_bank.reasons)

    res_scrape = HardFilterEngine.evaluate(scrape_job)
    assert res_scrape.eligible is False
    assert any("login" in r.lower() or "scraping" in r.lower() for r in res_scrape.reasons)

    # Even if Jev is rigged to return perfect 1.0 scores, hard filters MUST prevent ranking
    class MockPerfectJevProvider(JevProvider):
        def __init__(self):
            super().__init__(JevConfig(mode="assist", model="jev-pinned-1"))

        def decide(self, **kwargs) -> DecisionReceipt:
            return DecisionReceipt(
                use_case_id=SERVICE_JOB_FIT,
                question_set_version=SERVICE_JOB_FIT_VERSION,
                question_set_digest="dummy",
                model="jev-pinned-1",
                state_digest="dummy",
                answers={
                    q: {"type": "score", "score": 1.0, "probabilities": {"1.0": 1.0}, "confidence": 0.99}
                    for q in ("fleet_fit", "value", "clarity", "client_quality", "competition", "delivery_risk")
                },
                mode="assist",
                outcome="success",
            )

    ranker = JobRanker(provider=MockPerfectJevProvider())

    ranked_bank = ranker.rank_posting(bank_job)
    assert ranked_bank.eligible is False
    assert ranked_bank.rank == 0.0
    assert any(REASON_EXCLUDE_FINANCIAL_CONFLICT in r for r in ranked_bank.reasons)

    ranked_scrape = ranker.rank_posting(scrape_job)
    assert ranked_scrape.eligible is False
    assert ranked_scrape.rank == 0.0
    assert any(REASON_EXCLUDE_LOGIN_SCRAPING in r for r in ranked_scrape.reasons)


def test_hard_filters_all_categories_and_safe_exclusions():
    """Verify all 7 exclusion categories and ensure non-conflict terms like 'memory bank' are safe."""
    # Test safe exclusion
    memory_bank_job = JobPosting(
        source="file",
        id="safe-1",
        title="Build Vector Memory Bank for LLM Assistant",
        description="Implement an in-memory vector database and embedding memory bank in Python.",
        budget_type="fixed",
        budget_min=500.0,
        budget_max=500.0,
        skills=["Python", "Vector DB"],
    )
    res_mem = HardFilterEngine.evaluate(memory_bank_job)
    assert res_mem.eligible is True
    assert not any("bank" in r.lower() for r in res_mem.reasons)

    # Credit union / fintech lender conflict
    cu_job = JobPosting(
        source="file",
        id="cu-1",
        title="Credit Union Integration",
        description="Sync member data for a regional credit union and fintech lender.",
    )
    assert HardFilterEngine.evaluate(cu_job).eligible is False

    # Spam / Fake reviews
    spam_job = JobPosting(
        source="file",
        id="spam-1",
        title="Bot for Fake Reviews on Trustpilot",
        description="Automate posting fake reviews and bulk unsolicited DMs on Telegram.",
    )
    res_spam = HardFilterEngine.evaluate(spam_job)
    assert res_spam.eligible is False
    assert any(REASON_EXCLUDE_SPAM_FAKE_REVIEWS in r for r in res_spam.reasons)

    # Academic dishonesty
    acad_job = JobPosting(
        source="file",
        id="acad-1",
        title="Take my exam and do my university homework",
        description="Need someone to take my online computer science quiz and finish my assignment for class.",
    )
    res_acad = HardFilterEngine.evaluate(acad_job)
    assert res_acad.eligible is False
    assert any(REASON_EXCLUDE_ACADEMIC_DISHONESTY in r for r in res_acad.reasons)

    # Credentials / Account takeover
    cred_job = JobPosting(
        source="file",
        id="cred-1",
        title="Bypass OTP and account takeover script",
        description="Session hijacking tool to steal cookies and bypass 2fa on user accounts.",
    )
    res_cred = HardFilterEngine.evaluate(cred_job)
    assert res_cred.eligible is False
    assert any(REASON_EXCLUDE_CREDENTIAL_TAKEOVER in r for r in res_cred.reasons)

    # Adult / Gambling
    adult_job = JobPosting(
        source="file",
        id="adult-1",
        title="Automate OnlyFans and Casino Betting Bot",
        description="Adult entertainment script and sports betting poker bot.",
    )
    res_adult = HardFilterEngine.evaluate(adult_job)
    assert res_adult.eligible is False
    assert any(REASON_EXCLUDE_ADULT_GAMBLING in r for r in res_adult.reasons)

    # Physical hardware
    hw_job = JobPosting(
        source="file",
        id="hw-1",
        title="On-site only testing on client's physical hardware rack",
        description="Must be in-person on-site only; test on physical server rack and physical device in lab.",
    )
    res_hw = HardFilterEngine.evaluate(hw_job)
    assert res_hw.eligible is False
    assert any(REASON_EXCLUDE_PHYSICAL_HARDWARE in r for r in res_hw.reasons)

    # Budget flags
    low_fixed = JobPosting(
        source="file",
        id="low-1",
        title="Python script",
        description="write script",
        budget_type="fixed",
        budget_min=100.0,
        budget_max=100.0,
    )
    res_low = HardFilterEngine.evaluate(low_fixed)
    assert res_low.eligible is True
    assert any(REASON_FLAG_LOW_BUDGET in r for r in res_low.flags)

    low_hourly = JobPosting(
        source="file",
        id="low-2",
        title="Python script hourly",
        description="write script",
        budget_type="hourly",
        budget_min=20.0,
        budget_max=25.0,
    )
    res_low_h = HardFilterEngine.evaluate(low_hourly)
    assert res_low_h.eligible is False  # hourly work is never a one-off deliverable
    assert any(REASON_FLAG_LOW_BUDGET in r for r in res_low_h.flags)


def test_only_one_off_non_finance_deliverables_are_eligible():
    """Owner rule: 'build X and send it' only; no finance sector, hourly, ongoing or consulting."""
    from mco.jobs.filters import REASON_EXCLUDE_FINANCE_SECTOR, REASON_EXCLUDE_NOT_ONE_OFF

    def posting(title, description, budget_type="fixed"):
        return JobPosting(source="file", id=title, title=title, description=description,
                          budget_type=budget_type, budget_min=900.0, budget_max=900.0)

    finance = [
        ("Crypto trading bot", "Build a Binance\ntrading bot for day trading."),
        ("Loan app automation", "Automate our\nmortgage lending pipeline."),
        ("QuickBooks sync", "Sync invoices into QuickBooks for our bookkeeping team."),
        ("Insurance claims parser", "Parse insurance claim PDFs."),
        ("Investment dashboard", "Dashboard for our financial advisors."),
    ]
    for title, desc in finance:
        res = HardFilterEngine.evaluate(posting(title, desc))
        assert res.eligible is False, title
        assert REASON_EXCLUDE_FINANCE_SECTOR in res.reasons, title

    not_one_off = [
        ("Python developer", "Long-term\nengagement building internal tools."),
        ("Automation engineer", "Ongoing work, 20 hours per week."),
        ("AI consultant", "Looking for an AI consultant to advise our roadmap."),
        ("Join our team", "Join our team as a dedicated developer."),
        ("Monthly retainer", "Maintain our Zapier flows on a monthly retainer."),
        ("Script help", "Long term\nrelationship for the right person."),
        ("Automation dev", "Expect about 20 hrs/week."),
        ("Full-time role", "This is a full-time position."),
        ("VA needed", "Looking for a virtual assistant to manage inboxes."),
        ("AI advisor", "We need an AI advisor for our board."),
    ]
    for title, desc in not_one_off:
        res = HardFilterEngine.evaluate(posting(title, desc))
        assert res.eligible is False, title
        assert REASON_EXCLUDE_NOT_ONE_OFF in res.reasons, title

    hourly = HardFilterEngine.evaluate(posting("Build a Slack bot", "Build a Slack bot.", "hourly"))
    assert hourly.eligible is False
    assert REASON_EXCLUDE_NOT_ONE_OFF in hourly.reasons

    good = [
        ("Build a Gmail-to-Sheets automation", "Build a script that copies order emails into Google Sheets and hand it over."),
        ("Slack bot for our team", "Build a Slack bot that posts daily standup reminders to our team members."),
        ("PDF data extractor", "Extract tables from our product catalog PDFs into CSV. Deliver the script."),
        # False positives found in review of PR #116: benign build-and-deliver jobs.
        ("Photo DAM", "Build a digital asset management system for our photographers."),
        ("VA chatbot", "Build a virtual assistant chatbot for our website."),
        ("Booking page", "Booking page showing our office hours and a contact form."),
        ("Dev portal", "Build an in-house developer portal with docs search."),
        ("Coaching site", "Consultation booking form for my coaching business website."),
        ("One-time build", "One-time build, no ongoing maintenance needed."),
        ("Hours report", "Dashboard reporting hours per week by project."),
        ("Staff tool", "Our full-time staff needs a tool to track equipment."),
        ("Monthly report", "Automate our monthly report emails from a Google Sheet."),
        ("Career chatbot", "Build an AI career advisor chatbot for students."),
    ]
    for title, desc in good:
        res = HardFilterEngine.evaluate(posting(title, desc))
        assert res.eligible is True, (title, res.reasons)


# ── 3. Ranking Orders Clear $800 Automation Above Vague $50 Job ──────────────

def test_ranking_orders_clear_800_automation_above_vague_50_job():
    """Ranking orders a clear $800 automation job above a vague $50 one."""
    clear_800_job = JobPosting(
        source="upwork",
        id="good-800",
        title="Python API Automation and Integration with Pytest Suite",
        description=(
            "We require a robust Python automation pipeline to sync customer records between "
            "two SaaS systems using httpx and pydantic.\n"
            "Requirements and Deliverables:\n"
            "1. Core synchronization script with structured logging and retry logic.\n"
            "2. 90%+ unit and integration test coverage using pytest with recorded fixtures.\n"
            "3. GitHub Actions CI workflow configuration.\n"
            "Acceptance criteria: Clean run of pytest with zero errors; adherence to provided OpenAPI schema."
        ),
        budget_type="fixed",
        budget_min=800.0,
        budget_max=800.0,
        skills=["Python", "FastAPI", "pytest", "API Integration", "Automation"],
        client=ClientInfo(
            country="United States",
            payment_verified=True,
            total_spent=15000.0,
            hire_rate=0.80,
            rating=4.95,
        ),
        proposals_count=3,
        posted_at="2026-09-24T18:00:00Z",
        url="https://example.com/job/good-800",
    )

    vague_50_job = JobPosting(
        source="upwork",
        id="bad-50",
        title="Need script fast",
        description="quick script needed asap inbox me",
        budget_type="fixed",
        budget_min=50.0,
        budget_max=50.0,
        skills=["Python"],
        client=ClientInfo(
            country="United States",
            payment_verified=False,
            total_spent=0.0,
            hire_rate=0.0,
            rating=0.0,
        ),
        proposals_count=35,
        posted_at="2026-09-24T18:00:00Z",
        url="https://example.com/job/bad-50",
    )

    ranker = JobRanker(provider=None)  # Uses deterministic heuristic ranker
    ranked = ranker.rank_postings([vague_50_job, clear_800_job])

    assert len(ranked) == 2
    # The clear $800 job must be ranked #1
    assert ranked[0].posting.id == "good-800"
    assert ranked[1].posting.id == "bad-50"

    assert ranked[0].rank > 75.0, f"Expected high rank for clear $800 job, got {ranked[0].rank}"
    assert ranked[1].rank < 40.0, f"Expected low rank for vague $50 job, got {ranked[1].rank}"
    assert ranked[0].rank - ranked[1].rank > 35.0

    # Verify score components
    assert ranked[0].scores["fleet_fit"] > ranked[1].scores["fleet_fit"]
    assert ranked[0].scores["value"] > ranked[1].scores["value"]
    assert ranked[0].scores["clarity"] > ranked[1].scores["clarity"]
    assert ranked[0].scores["client_quality"] > ranked[1].scores["client_quality"]

    # Verify low budget flag is captured in reasons for vague job
    assert any(REASON_FLAG_LOW_BUDGET in r for r in ranked[1].reasons)


# ── 4. Jev Failure Uses Fallback and Marks It ────────────────────────────────

def test_jev_failure_uses_fallback_and_marks_it():
    """Jev timeouts or failures fall back to deterministic heuristic ranker and mark it."""
    posting = JobPosting(
        source="file",
        id="test-1",
        title="Python ETL Automation",
        description="Build an automated data pipeline using Python, SQLite, and pytest.",
        budget_type="fixed",
        budget_min=600.0,
        budget_max=600.0,
        skills=["Python", "SQLite"],
        client=ClientInfo(country="Canada", payment_verified=True, rating=4.8),
    )

    # 1. Test with a provider whose HTTP call fails/times out
    class TimeoutMockProvider(JevProvider):
        def __init__(self):
            super().__init__(JevConfig(mode="assist", model="jev-pinned-1"))

        def decide(self, **kwargs) -> DecisionReceipt:
            # Simulate Jev timeout / connection failure
            return DecisionReceipt(
                use_case_id=SERVICE_JOB_FIT,
                question_set_version=SERVICE_JOB_FIT_VERSION,
                question_set_digest="dummy_digest",
                model="jev-pinned-1",
                state_digest="state_digest",
                mode="assist",
                outcome="fallback",
                error_class="timeout",
                latency_ms=5000,
            )

    ranker_timeout = JobRanker(provider=TimeoutMockProvider())
    ranked = ranker_timeout.rank_posting(posting)

    assert ranked.rank_source == "heuristic_fallback"
    assert ranked.fallback_reason is not None
    assert "timeout" in ranked.fallback_reason.lower() or "fallback" in ranked.fallback_reason.lower()
    assert ranked.receipt is not None
    assert ranked.receipt["outcome"] == "fallback"
    assert ranked.receipt["error_class"] == "timeout"
    assert 0.0 < ranked.rank <= 100.0

    # 2. Test with provider is None (disabled / unconfigured)
    ranker_none = JobRanker(provider=None)
    ranked_none = ranker_none.rank_posting(posting)

    assert ranked_none.rank_source == "heuristic_fallback"
    assert ranked_none.fallback_reason == "Jev provider disabled or unconfigured"
    assert 0.0 < ranked_none.rank <= 100.0


# ── 5. Importer Normalizes Upwork GraphQL Response and CSV ───────────────────

def test_importer_normalizes_upwork_graphql_fixture():
    """Importer normalizes recorded Upwork GraphQL response fixture according to schema."""
    fixture_path = FIXTURES_DIR / "upwork_graphql_response.json"
    assert fixture_path.is_file(), "Upwork GraphQL fixture file must exist"

    raw_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    postings = UpworkGraphQLAdapter.normalize_graphql_response(raw_data)

    assert len(postings) == 4

    # Job 1: Fixed price automation job
    p1 = postings[0]
    assert p1.source == "upwork"
    assert p1.id == "~01a1b2c3d4e5f6g7h8"
    assert "Python Automation" in p1.title
    assert p1.budget_type == "fixed"
    assert p1.budget_min == 800.0
    assert p1.budget_max == 800.0
    assert "pytest" in p1.skills
    assert p1.client.country == "United States"
    assert p1.client.payment_verified is True
    assert p1.client.total_spent == 12500.0
    assert p1.client.hire_rate == 0.85
    assert p1.client.rating == 4.95
    assert p1.proposals_count == 3
    assert p1.url == "https://www.upwork.com/jobs/~01a1b2c3d4e5f6g7h8"

    # Job 2: Bank job
    p2 = postings[1]
    assert "First National Bank" in p2.title
    assert p2.budget_type == "fixed"
    assert p2.budget_max == 1500.0

    # Job 3: Scraping job
    p3 = postings[2]
    assert "Scrape LinkedIn" in p3.title
    assert p3.budget_max == 600.0

    # Job 4: Hourly job
    p4 = postings[3]
    assert p4.budget_type == "hourly"
    assert p4.budget_min == 45.0
    assert p4.budget_max == 65.0
    assert p4.client.country == "Canada"


def test_importer_normalizes_csv():
    """Importer normalizes CSV fixture into JobPosting objects."""
    fixture_path = FIXTURES_DIR / "sample_postings.csv"
    assert fixture_path.is_file(), "CSV fixture must exist"

    postings = import_from_csv(fixture_path)
    assert len(postings) == 4

    p1 = postings[0]
    assert p1.id == "job-101"
    assert "Automated Test Suite in Pytest" in p1.title
    assert p1.budget_type == "fixed"
    assert p1.budget_min == 800.0
    assert p1.budget_max == 800.0
    assert "pytest" in p1.skills
    assert p1.client.country == "United States"
    assert p1.client.payment_verified is True
    assert p1.client.total_spent == 15000.0
    assert p1.client.hire_rate == 0.80
    assert p1.client.rating == 4.9
    assert p1.proposals_count == 2

    # Test load_postings_from_file auto-detection
    postings_auto = load_postings_from_file(fixture_path)
    assert len(postings_auto) == 4
    assert postings_auto[0].id == "job-101"


# ── 6. Upwork Adapter Token Custody & Secret Handling ────────────────────────

def test_upwork_adapter_token_custody_and_never_logged(monkeypatch):
    """Upwork adapter reads token from env/vault without logging and refuses unconfigured search."""
    # Ensure env is empty
    monkeypatch.delenv("UPWORK_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("UPWORK_TOKEN", raising=False)
    monkeypatch.delenv("MCO_UPWORK_TOKEN", raising=False)

    adapter = UpworkGraphQLAdapter()
    assert adapter.is_configured is False

    with pytest.raises(UpworkAuthenticationError) as exc_info:
        adapter.search_jobs()
    assert "not configured" in str(exc_info.value)
    # Ensure no token pattern is leaked
    assert "Bearer" not in str(exc_info.value)

    # Set token in environment
    secret_token = "secret-oauth2-token-xyz987"
    monkeypatch.setenv("UPWORK_ACCESS_TOKEN", secret_token)

    adapter_configured = UpworkGraphQLAdapter()
    assert adapter_configured.is_configured is True

    # Check that string representation does not leak the secret token
    repr_str = str(adapter_configured.__dict__)
    assert secret_token not in str(repr_str).replace(secret_token, "[REDACTED]")

    # Mock transport to test search_jobs against recorded fixture
    fixture_data = json.loads((FIXTURES_DIR / "upwork_graphql_response.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request):
        assert request.headers.get("Authorization") == f"Bearer {secret_token}"
        return httpx.Response(200, json=fixture_data)

    transport = httpx.MockTransport(handler)
    adapter_with_transport = UpworkGraphQLAdapter(token=secret_token, transport=transport)
    results = adapter_with_transport.search_jobs(query="python", limit=10)
    assert len(results) == 4


# ── 7. CLI Test: `mco jobs rank` ─────────────────────────────────────────────

def test_cli_jobs_rank_command(tmp_path):
    """Test `mco jobs rank postings.csv --top 2 --out ranked.json`."""
    csv_file = FIXTURES_DIR / "sample_postings.csv"
    out_file = tmp_path / "ranked.json"

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["jobs", "rank", str(csv_file), "--top", "3", "--out", str(out_file)],
    )

    assert result.exit_code == 0
    # Check Markdown table columns
    assert "| Rank | Title | Budget | Fit | Value | Safety | Reasons | URL |" in result.output
    assert "Automated Test Suite in Pytest" in result.output

    # Check output JSON file
    assert out_file.is_file()
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert isinstance(saved, list)
    assert len(saved) == 4

    top_item = saved[0]
    assert "rank" in top_item
    assert "posting" in top_item
    assert "scores" in top_item
    assert "eligible" in top_item
    assert "receipt" in top_item
    assert top_item["posting"]["id"] == "job-101"


# ── 8. Review Findings Regression Tests (PR #114) ────────────────────────────

def test_hard_filter_evasion_multiline_description_excluded():
    """Multi-line descriptions asking to scrape behind login or paywalls must be EXCLUDED."""
    multiline_scrape_job = JobPosting(
        source="upwork",
        id="multi-scrape-1",
        title="Python Data Extractor",
        description=(
            "We need a developer to write a scraping script in Python.\n"
            "The data is located behind login pages and protected by Cloudflare.\n"
            "You will need to pass session cookies to extract the records."
        ),
        budget_type="fixed",
        budget_min=1000.0,
        budget_max=1000.0,
        skills=["Python", "Scraping"],
        client=ClientInfo(country="United States", payment_verified=True, rating=5.0),
    )
    result = HardFilterEngine.evaluate(multiline_scrape_job)
    assert result.eligible is False
    assert any(REASON_EXCLUDE_LOGIN_SCRAPING in r for r in result.reasons)


def test_score_normalization_explicit_scale_and_realistic_receipt():
    """Score normalization must explicitly use 0-4 rubric scale (divide by 4.0), not ambiguous threshold."""
    assert RUBRIC_SCORE_MAX == 4.0
    assert normalize_rubric_score(0.0) == 0.0
    assert normalize_rubric_score(1.0) == 0.25
    assert normalize_rubric_score(2.0) == 0.50
    assert normalize_rubric_score(3.0) == 0.75
    assert normalize_rubric_score(4.0) == 1.00
    assert normalize_rubric_score(2.5) == 0.625

    posting = JobPosting(
        source="upwork",
        id="norm-1",
        title="FastAPI Automation Service",
        description="Build and test a well-defined FastAPI automation service with pytest.",
        budget_type="fixed",
        budget_min=2000.0,
        budget_max=2000.0,
        skills=["Python", "FastAPI"],
        client=ClientInfo(country="United States", payment_verified=True, rating=5.0),
    )

    realistic_receipt = DecisionReceipt(
        use_case_id=SERVICE_JOB_FIT,
        question_set_version=SERVICE_JOB_FIT_VERSION,
        question_set_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        model="jev-1.13.0",
        state_digest="a1b2c3d4e5f6",
        answers={
            "fleet_fit": {
                "type": "score",
                "score": 4.0,  # Level 4 -> normalized 1.00
                "confidence": 0.95,
                "legend": {str(i): f"level {i}" for i in range(5)},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 1.0},
            },
            "value": {
                "type": "score",
                "score": 3.0,  # Level 3 -> normalized 0.75
                "confidence": 0.88,
                "legend": {str(i): f"level {i}" for i in range(5)},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.0, "3": 1.0, "4": 0.0},
            },
            "clarity": {
                "type": "score",
                "score": 2.0,  # Level 2 -> normalized 0.50
                "confidence": 0.75,
                "legend": {str(i): f"level {i}" for i in range(5)},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0, "4": 0.0},
            },
            "client_quality": {
                "type": "score",
                "score": 1.0,  # Level 1 -> normalized 0.25 (previously would have been 1.0!)
                "confidence": 0.80,
                "legend": {str(i): f"level {i}" for i in range(5)},
                "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0, "3": 0.0, "4": 0.0},
            },
            "competition": {
                "type": "score",
                "score": 2.5,  # 2.5 -> normalized 0.625
                "confidence": 0.65,
                "legend": {str(i): f"level {i}" for i in range(5)},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.5, "3": 0.5, "4": 0.0},
            },
            "delivery_risk": {
                "type": "score",
                "score": 3.5,  # 3.5 -> normalized 0.875
                "confidence": 0.70,
                "legend": {str(i): f"level {i}" for i in range(5)},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.5, "4": 0.5},
            },
        },
        probabilities={
            q: {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 1.0}
            for q in ("fleet_fit", "value", "clarity", "client_quality", "competition", "delivery_risk")
        },
        confidence={
            q: 0.90 for q in ("fleet_fit", "value", "clarity", "client_quality", "competition", "delivery_risk")
        },
        latency_ms=120,
        usage={"input_tokens": 420, "output_tokens": 85},
        request_id="req_realistic_test_123",
        mode="assist",
        outcome="success",
    )

    class RealisticJevProvider(JevProvider):
        def __init__(self):
            super().__init__(JevConfig(mode="assist", model="jev-1.13.0"))

        def decide(self, **kwargs) -> DecisionReceipt:
            return realistic_receipt

    ranker = JobRanker(provider=RealisticJevProvider())
    ranked = ranker.rank_posting(posting)

    assert ranked.rank_source == "jev"
    assert ranked.scores["fleet_fit"] == 1.00
    assert ranked.scores["value"] == 0.75
    assert ranked.scores["clarity"] == 0.50
    assert ranked.scores["client_quality"] == 0.25
    assert ranked.scores["competition"] == 0.625
    assert ranked.scores["delivery_risk"] == 0.875
    assert ranked.receipt["request_id"] == "req_realistic_test_123"


def test_shadow_mode_answers_must_not_rank():
    """Shadow-mode answers must not rank: fall back to heuristic, record reason on row, keep receipt."""
    posting = JobPosting(
        source="upwork",
        id="shadow-1",
        title="Python Automation Tool",
        description="Build an automation script with pytest test suite.",
        budget_type="fixed",
        budget_min=1000.0,
        budget_max=1000.0,
        skills=["Python"],
        client=ClientInfo(country="United States", payment_verified=True, rating=5.0),
    )

    shadow_receipt = DecisionReceipt(
        use_case_id=SERVICE_JOB_FIT,
        question_set_version=SERVICE_JOB_FIT_VERSION,
        question_set_digest="dummy",
        model="jev-pinned-1",
        state_digest="dummy",
        answers={
            q: {"type": "score", "score": 4.0, "probabilities": {"4": 1.0}, "confidence": 0.99}
            for q in ("fleet_fit", "value", "clarity", "client_quality", "competition", "delivery_risk")
        },
        mode="shadow",
        outcome="shadow",
    )

    class ShadowMockProvider(JevProvider):
        def __init__(self):
            super().__init__(JevConfig(mode="shadow", model="jev-pinned-1"))

        def decide(self, **kwargs) -> DecisionReceipt:
            return shadow_receipt

    ranker = JobRanker(provider=ShadowMockProvider())
    ranked = ranker.rank_posting(posting)

    assert ranked.rank_source == "heuristic_fallback"
    assert ranked.fallback_reason is not None
    assert "shadow" in ranked.fallback_reason.lower()
    # Scores must match heuristic, not the 4.0 Jev scores
    heuristic = deterministic_heuristic_scores(posting)
    assert ranked.scores == heuristic
    # Receipt must still be preserved for audit
    assert ranked.receipt is not None
    assert ranked.receipt["mode"] == "shadow"
    assert ranked.receipt["outcome"] == "shadow"
