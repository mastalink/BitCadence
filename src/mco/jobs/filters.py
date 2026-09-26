"""Deterministic hard filters in code for job ranking.

Hard filters represent non-negotiable boundaries (employment conflicts, legal
and terms-of-service compliance, ethical boundaries, and fleet capabilities).
They are evaluated deterministically in code and are NEVER delegated to Jev.

The `\\bbank\\b` filter is deliberately conservative (it may exclude benign
"bank statement parser" jobs), because Joseph works at the Federal Reserve.
The wider finance-sector filter (trading, crypto, lending, insurance,
accounting, payments) is conservative for the same reason.

Only one-off deliverables are eligible ("build X and send it"). Hourly
contracts, ongoing or long-term engagements, retainers, staff roles and
consulting or advisory work are excluded (owner decision, 2026-09-25).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

from mco.jobs.models import JobPosting


REASON_EXCLUDE_FINANCIAL_CONFLICT = (
    "EXCLUDE: Client is a bank/credit union/financial institution/fintech lender "
    "(owner's Federal Reserve employment conflict)"
)
REASON_EXCLUDE_FINANCE_SECTOR = (
    "EXCLUDE: Finance-sector work (trading, crypto, lending, insurance, accounting, "
    "payments; owner's Federal Reserve employment conflict)"
)
REASON_EXCLUDE_NOT_ONE_OFF = (
    "EXCLUDE: Not a one-off deliverable (hourly, ongoing, long-term, retainer, "
    "staff role, or consulting/advisory)"
)
REASON_EXCLUDE_LOGIN_SCRAPING = (
    "EXCLUDE: Scraping behind logins, paywalls, or bot challenges "
    "(marketplace terms violation)"
)
REASON_EXCLUDE_SPAM_FAKE_REVIEWS = (
    "EXCLUDE: Spam, mass messaging, or fake review manipulation"
)
REASON_EXCLUDE_ACADEMIC_DISHONESTY = (
    "EXCLUDE: Academic dishonesty, homework, exams, or coursework assistance"
)
REASON_EXCLUDE_CREDENTIAL_TAKEOVER = (
    "EXCLUDE: Requests for credentials, account access, or account takeover"
)
REASON_EXCLUDE_ADULT_GAMBLING = (
    "EXCLUDE: Adult content, pornography, casino, or gambling/betting services"
)
REASON_EXCLUDE_PHYSICAL_HARDWARE = (
    "EXCLUDE: Work requires client's physical hardware or on-site presence not available to fleet"
)
REASON_FLAG_LOW_BUDGET = (
    "FLAG: Budget below minimum threshold (<$300 fixed or <$35/h)"
)
REASON_FLAG_NO_BUDGET = (
    "FLAG: Budget unstated or missing"
)

# Minimum acceptable budget thresholds
MIN_FIXED_BUDGET = 300.0
MIN_HOURLY_BUDGET = 35.0

_RE_FLAGS = re.IGNORECASE | re.DOTALL

# Regex patterns for deterministic matching (case-insensitive, dotall for multi-line support)
_FINANCIAL_PATTERNS = [
    re.compile(r"\b(?:credit\s+union|fintech\s+lender|mortgage\s+lender|commercial\s+bank|investment\s+bank|retail\s+bank|depository\s+institution|federal\s+reserve)\b", _RE_FLAGS),
    re.compile(r"\b(?:first\s+national\s+bank|wells\s+fargo|chase\s+bank|bank\s+of\s+america|citibank|capital\s+one|goldman\s+sachs|morgan\s+stanley)\b", _RE_FLAGS),
    re.compile(r"\b(?:bank|banking)\b", _RE_FLAGS),
]

_FINANCIAL_SAFE_EXCLUSIONS = re.compile(
    r"\b(?:memory\s+bank|question\s+bank|word\s+bank|blood\s+bank|food\s+bank|power\s+bank|battery\s+bank)\b",
    _RE_FLAGS,
)

_FINANCE_SECTOR_PATTERNS = [
    re.compile(r"\b(?:finance|financial|fintech|financing|investment|investing|investor\s+portfolio|wealth\s+management|hedge\s+fund|private\s+equity|venture\s+capital)\b", _RE_FLAGS),
    re.compile(r"\b(?:trading\s+(?:bot|platform|strategy|algorithm|signals?|system)|algorithmic\s+trading|algo\s+trading|day\s+trading|forex|stock\s+market|stocks?\s+(?:trading|screener|analysis)|options\s+trading|brokerage|broker-dealer)\b", _RE_FLAGS),
    re.compile(r"\b(?:crypto|cryptocurrency|bitcoin|ethereum|defi|nft|token\s+sale|web3|blockchain)\b", _RE_FLAGS),
    re.compile(r"\b(?:loans?|lending|lender|mortgage|credit\s+(?:card|score|repair|report)|debt\s+collection|underwriting|insurance|insurer)\b", _RE_FLAGS),
    re.compile(r"\b(?:accounting|bookkeeping|bookkeeper|accountant|payroll|tax\s+(?:return|preparation|filing)|quickbooks|xero|invoice\s+factoring)\b", _RE_FLAGS),
    re.compile(r"\b(?:payment\s+(?:processor|processing|gateway)|money\s+transfer|remittance|kyc|aml|anti-money\s+laundering)\b", _RE_FLAGS),
]

_NOT_ONE_OFF_PATTERNS = [
    # Negated mentions ("one-time build, no ongoing maintenance") stay eligible.
    re.compile(r"(?<!\bno\s)(?<!\bnot\s)(?<!\bwithout\s)(?<!\bnon-)\b(?:long[\s-]term|ongoing|on-going|retainer|recurring\s+work|continuous\s+(?:work|support)|monthly\s+(?:retainer|contract|fee))\b", _RE_FLAGS),
    # Role-adjacent only, so "our full-time staff needs a tool" stays eligible.
    re.compile(r"\b(?:(?:full|part)[\s-]time\s+(?:role|position|job|developer|engineer|contractor|freelancer|commitment|hire|basis)|(?:work|available|hire|hiring)\s+(?:full|part)[\s-]time|\d+\s*(?:\+\s*)?(?:hours?|hrs?)\s*(?:per|a|/)\s*(?:week|wk|month))\b", _RE_FLAGS),
    re.compile(r"\b(?:join\s+our\s+team|in-house\s+(?:role|position)|staff\s+augmentation|dedicated\s+(?:developer|engineer|resource)|(?:hire|hiring|looking\s+for|need|seeking)\s+(?:an?\s+)?virtual\s+assistant|contract[\s-]to[\s-]hire|hire\s+for\s+(?:a\s+)?(?:role|position))\b", _RE_FLAGS),
    # Consulting as the engagement itself, not a product that mentions it
    # ("consultation booking form for my coaching business" stays eligible).
    re.compile(r"\b(?:consultant|consulting\s+(?:role|engagement|services|call)|advisory\s+(?:role|services|engagement)|(?:hire|hiring|looking\s+for|need|seeking)\s+(?:an?\s+)?(?:\w+\s+)?advisor|fractional\s+(?:cto|cio|engineer)|(?:hold|host|offer)\s+office\s+hours)\b", _RE_FLAGS),
]

_LOGIN_SCRAPING_PATTERNS = [
    re.compile(r"\b(?:scrape|scraping|crawler|crawling|extractor|extract)\b.*\b(?:behind\s+login|after\s+login|authenticated|with\s+login|login\s+session|session\s+cookie|logged\s+in|bypassing\s+login|bypass\s+login|login\s+screen|login\s+wall|behind\s+paywall|paywall)\b", _RE_FLAGS),
    re.compile(r"\b(?:behind\s+login|after\s+login|login\s+session|logged\s+in|behind\s+paywall|paywall)\b.*\b(?:scrape|scraping|crawler|crawling|extract)\b", _RE_FLAGS),
    re.compile(r"\b(?:bypass|bypassing|solve|solving|crack|handle|circumvent)\b.*\b(?:login|auth|authentication|captcha|recaptcha|hcaptcha|cloudflare|datadome|bot\s+challenge|anti-bot|paywall)\b", _RE_FLAGS),
    re.compile(r"\b(?:paywall|bot\s+challenge|anti-bot|cloudflare|captcha|login\s+wall)\b.*\b(?:bypass|bypassing|circumvent|cracker|scrape|scraping)\b", _RE_FLAGS),
    re.compile(r"\b(?:scrape|scraping)\b.*\b(?:linkedin|instagram|facebook|tiktok)\b", _RE_FLAGS),
    re.compile(r"\b(?:behind\s+paywall|behind\s+login)\b", _RE_FLAGS),
]

_SPAM_FAKE_REVIEWS_PATTERNS = [
    re.compile(r"\b(?:fake|paid|fictitious|manipulate[d]?|post[ing]?)\b.*\b(?:reviews?|ratings?|testimonials?)\b", _RE_FLAGS),
    re.compile(r"\b(?:trustpilot|amazon|google\s+maps|yelp)\b.*\b(?:reviews?|ratings?)\b.*\b(?:post|bot|boost|fake|generate)\b", _RE_FLAGS),
    re.compile(r"\b(?:mass|bulk|unsolicited)\b.*\b(?:dm|dms|direct\s+messages?|messages?|messaging|emails?|cold\s+outreach)\b", _RE_FLAGS),
    re.compile(r"\b(?:cold\s+dm\s+bot|spam\s+bot|telegram\s+mass|whatsapp\s+mass|sms\s+blast)\b", _RE_FLAGS),
]

_ACADEMIC_DISHONESTY_PATTERNS = [
    re.compile(r"\b(?:do\s+my|finish\s+my|write\s+my|take\s+my)\b.*\b(?:homework|assignment|exam|quiz|test|thesis|dissertation)\b", _RE_FLAGS),
    re.compile(r"\b(?:homework|assignment|exam|quiz|test|thesis|dissertation)\b.*\b(?:for\s+class|for\s+university|for\s+college|for\s+school|take\s+it\s+for\s+me)\b", _RE_FLAGS),
    re.compile(r"\b(?:academic\s+dishonesty|cheating\s+software|exam\s+bypass)\b", _RE_FLAGS),
]

_CREDENTIAL_TAKEOVER_PATTERNS = [
    re.compile(r"\b(?:account\s+takeover|credential\s+stuffing|session\s+hijack(?:ing)?|steal\s+cookie[s]?|steal\s+token[s]?)\b", _RE_FLAGS),
    re.compile(r"\b(?:bypass|intercept)\b.*\b(?:2fa|mfa|otp|two-factor|one-time\s+password)\b", _RE_FLAGS),
    re.compile(r"\b(?:buy|rent|share|borrow)\b.*\b(?:upwork|fiverr|freelancer)\s+account\b", _RE_FLAGS),
    re.compile(r"\b(?:give\s+me\s+your|send\s+me\s+your|need\s+your)\b.*\b(?:credentials|passwords?|login\s+details|bank\s+login)\b", _RE_FLAGS),
]

_ADULT_GAMBLING_PATTERNS = [
    re.compile(r"\b(?:porn|pornography|adult\s+entertainment|adult\s+website|erotic|escort\s+service|nsfw|onlyfans)\b", _RE_FLAGS),
    re.compile(r"\b(?:casino|gambling|sports\s+betting|betting\s+bot|poker\s+bot|roulette\s+bot|slot\s+machine)\b", _RE_FLAGS),
]

_PHYSICAL_HARDWARE_PATTERNS = [
    re.compile(r"\b(?:on-site\s+only|onsite\s+only|in-person\s+only|physical\s+presence\s+required)\b", _RE_FLAGS),
    re.compile(r"\b(?:client(?:'s)?\s+physical\s+hardware|physical\s+hardware\s+we\s+don't\s+have)\b", _RE_FLAGS),
    re.compile(r"\b(?:test\s+on|must\s+have)\b.*\b(?:physical\s+iphone|physical\s+android|physical\s+device|physical\s+car|physical\s+vehicle|server\s+rack|on-premise\s+hardware)\b", _RE_FLAGS),
    re.compile(r"\b(?:rack\s+mount|hardware\s+lab|physical\s+soldering|breadboard)\b", _RE_FLAGS),
]


@dataclass
class FilterResult:
    eligible: bool
    reasons: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)


class HardFilterEngine:
    """Evaluates deterministic exclusion rules and flags for job postings."""

    @classmethod
    def evaluate(cls, posting: JobPosting) -> FilterResult:
        full_text = f"{posting.title}\n{posting.description}\n{' '.join(posting.skills)}"
        client_text = f"{posting.client.country or ''}"

        reasons: List[str] = []
        flags: List[str] = []

        # 1. Bank / Credit Union / Financial Institution / Fintech Lender conflict
        # Check safe exclusions first (e.g. "memory bank")
        has_safe_exclusion = bool(_FINANCIAL_SAFE_EXCLUSIONS.search(full_text))
        for pattern in _FINANCIAL_PATTERNS:
            if pattern.search(full_text) or pattern.search(client_text):
                # If matched pattern is generic "bank" but safe exclusion matched, check specifically
                if pattern.pattern == r"\b(?:bank|banking)\b" and has_safe_exclusion:
                    # check if other financial terms exist
                    stripped = _FINANCIAL_SAFE_EXCLUSIONS.sub("", full_text)
                    if not pattern.search(stripped):
                        continue
                reasons.append(REASON_EXCLUDE_FINANCIAL_CONFLICT)
                break

        # 1b. Wider finance sector (trading, crypto, lending, insurance, accounting)
        if REASON_EXCLUDE_FINANCIAL_CONFLICT not in reasons:
            for pattern in _FINANCE_SECTOR_PATTERNS:
                if pattern.search(full_text):
                    reasons.append(REASON_EXCLUDE_FINANCE_SECTOR)
                    break

        # 1c. One-off deliverables only: no hourly, ongoing, staff or consulting work
        if (posting.budget_type or "").strip().lower() == "hourly":
            reasons.append(REASON_EXCLUDE_NOT_ONE_OFF)
        else:
            for pattern in _NOT_ONE_OFF_PATTERNS:
                if pattern.search(full_text):
                    reasons.append(REASON_EXCLUDE_NOT_ONE_OFF)
                    break

        # 2. Scraping behind logins / paywalls / bot challenges
        for pattern in _LOGIN_SCRAPING_PATTERNS:
            if pattern.search(full_text):
                reasons.append(REASON_EXCLUDE_LOGIN_SCRAPING)
                break

        # 3. Spam / mass messaging / fake reviews
        for pattern in _SPAM_FAKE_REVIEWS_PATTERNS:
            if pattern.search(full_text):
                reasons.append(REASON_EXCLUDE_SPAM_FAKE_REVIEWS)
                break

        # 4. Academic dishonesty
        for pattern in _ACADEMIC_DISHONESTY_PATTERNS:
            if pattern.search(full_text):
                reasons.append(REASON_EXCLUDE_ACADEMIC_DISHONESTY)
                break

        # 5. Credentials or account takeover
        for pattern in _CREDENTIAL_TAKEOVER_PATTERNS:
            if pattern.search(full_text):
                reasons.append(REASON_EXCLUDE_CREDENTIAL_TAKEOVER)
                break

        # 6. Adult or gambling
        for pattern in _ADULT_GAMBLING_PATTERNS:
            if pattern.search(full_text):
                reasons.append(REASON_EXCLUDE_ADULT_GAMBLING)
                break

        # 7. Physical hardware / on-site
        for pattern in _PHYSICAL_HARDWARE_PATTERNS:
            if pattern.search(full_text):
                reasons.append(REASON_EXCLUDE_PHYSICAL_HARDWARE)
                break

        # Hard exclusions make posting ineligible
        is_eligible = len(reasons) == 0

        # Budget checks (flagging, recorded in reasons & flags)
        budget_flag = cls._check_budget_threshold(posting)
        if budget_flag:
            flags.append(budget_flag)
            reasons.append(budget_flag)

        return FilterResult(
            eligible=is_eligible,
            reasons=reasons,
            flags=flags,
        )

    @classmethod
    def _check_budget_threshold(cls, posting: JobPosting) -> str | None:
        btype = (posting.budget_type or "").strip().lower()
        bmax = posting.budget_max
        bmin = posting.budget_min
        effective = bmax if bmax is not None else bmin

        if btype == "fixed":
            if effective is not None:
                if effective < MIN_FIXED_BUDGET:
                    return f"{REASON_FLAG_LOW_BUDGET} (${effective:.0f} < ${MIN_FIXED_BUDGET:.0f})"
            else:
                return REASON_FLAG_NO_BUDGET
        elif btype == "hourly":
            if effective is not None:
                if effective < MIN_HOURLY_BUDGET:
                    return f"{REASON_FLAG_LOW_BUDGET} (${effective:.0f}/h < ${MIN_HOURLY_BUDGET:.0f}/h)"
            else:
                return REASON_FLAG_NO_BUDGET
        else:
            if effective is not None and effective < MIN_FIXED_BUDGET:
                return f"{REASON_FLAG_LOW_BUDGET} (${effective:.0f} < ${MIN_FIXED_BUDGET:.0f})"
            elif effective is None:
                return REASON_FLAG_NO_BUDGET

        return None
