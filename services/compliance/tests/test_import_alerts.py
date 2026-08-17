"""Import certification and news — what each is allowed to conclude.

docs/compliance-usecases.md §4.1, §4.2 and above all §4.3. Two rules carry the
whole design here and neither is a preference:

* only a government source sets red, so news can raise yellow and nothing more;
* an import alert is matched to a manufacturer by name, so the match has to be
  exact — a false positive is a public accusation against a named company.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from medstock_shared.certification import (
    AlertListing,
    NewsItem,
    Severity,
    Status,
    evaluate,
    firm_key,
    status_for,
)


def listing(**kw) -> AlertListing:
    base = {
        "alert_number": "66-40",
        "firm_name": "Aruba Aloe Balm N.V.",
        "country": "ARUBA",
        "listed_at": date(2024, 4, 15),
        "source_url": "https://www.accessdata.fda.gov/cms_ia/importalert_189.html",
    }
    return AlertListing(**{**base, **kw})


def codes(findings) -> set[str]:
    return {f.code for f in findings}


# --- firm_key: exact, and no more than exact ---------------------------------


def test_corporate_suffixes_do_not_change_identity():
    """ "Aruba Aloe Balm N.V." and "ARUBA ALOE BALM NV" are one firm."""
    assert firm_key("Aruba Aloe Balm N.V.") == firm_key("ARUBA ALOE BALM NV")
    assert firm_key("Acme Pharma, Inc.") == firm_key("acme pharma inc")
    assert firm_key("Sun Pharmaceutical Industries Ltd") == firm_key(
        "SUN PHARMACEUTICAL INDUSTRIES"
    )


def test_different_firms_do_not_collide():
    assert firm_key("Sun Pharma") != firm_key("Sun Pharmaceutical Industries")
    assert firm_key("Acme Labs") != firm_key("Acme Laboratories")


def test_matching_is_not_fuzzy():
    """The load-bearing negative. A missed alert looks like every drug that is
    not on one; a false match accuses a named manufacturer of being detained at
    the border over a product that has nothing to do with them. Substring and
    near-miss matching are therefore deliberately absent."""
    assert firm_key("Acme") != firm_key("Acme Pharma")
    assert firm_key("Acme Pharma") != firm_key("Acme Pharma Europe")
    assert firm_key("Teva") != firm_key("Teva Czech Industries")


def test_a_name_that_is_only_a_suffix_survives():
    """Stripping every token would make two unrelated firms equal on empty."""
    assert firm_key("Limited") != ""


# --- import alerts raise yellow, not red -------------------------------------


def test_a_listed_labeler_raises_yellow():
    findings = [f for f in evaluate(import_alerts=[listing()]) if f.code.startswith("IMPORT_")]
    assert codes(findings) == {"IMPORT_ALERT_GMP"}
    assert findings[0].severity is Severity.YELLOW
    assert status_for(findings) is Status.YELLOW


def test_the_finding_names_the_firm_and_the_alert():
    """This is the one finding in the system that is about a company rather
    than a product, so a pharmacist has to be able to check the claim."""
    message = evaluate(import_alerts=[listing()])[0].message
    assert "Aruba Aloe Balm N.V." in message
    assert "66-40" in message
    assert "ARUBA" in message
    assert "2024-04-15" in message


def test_the_two_alerts_are_different_findings():
    """66-40 is a CGMP failure, 66-41 is an unapproved drug. Same colour, very
    different conversation with the supplier."""
    gmp = [
        f
        for f in evaluate(import_alerts=[listing(alert_number="66-40")])
        if f.code.startswith("IMPORT_")
    ]
    unapproved = [
        f
        for f in evaluate(import_alerts=[listing(alert_number="66-41")])
        if f.code.startswith("IMPORT_")
    ]
    assert codes(gmp) == {"IMPORT_ALERT_GMP"}
    assert codes(unapproved) == {"IMPORT_ALERT_UNAPPROVED"}


def test_an_unknown_alert_number_is_ignored_rather_than_guessed():
    """FDA publishes dozens of import alerts. Only the two this design covers
    have an agreed meaning here; the rest must not be given one."""
    findings = evaluate(import_alerts=[listing(alert_number="99-99")])
    assert not [f for f in findings if f.code.startswith("IMPORT_")]


def test_an_import_alert_is_persistent_not_transient():
    """Detention without physical examination is a standing posture, not an
    event with an end. Only transient findings deserve to interrupt anyone."""
    from medstock_shared.certification import RULES

    assert RULES["IMPORT_ALERT_GMP"].transient is False


# --- news stops at yellow, always --------------------------------------------


def test_news_raises_yellow():
    findings = [
        f
        for f in evaluate(news=[NewsItem(headline="Recall widens", url="https://x.test/1")])
        if f.code == "NEWS_SIGNAL"
    ]
    assert codes(findings) == {"NEWS_SIGNAL"}
    assert findings[0].severity is Severity.YELLOW


def test_no_amount_of_news_can_turn_a_badge_red():
    """§4.3 as a property, not a promise. Acting on an article as fact would
    let the system tell a pharmacist a drug is uncertified because a blog said
    so."""
    findings = evaluate(
        news=[NewsItem(headline=f"Story {i}", url=f"https://x.test/{i}") for i in range(25)]
    )
    assert len([f for f in findings if f.code == "NEWS_SIGNAL"]) == 25
    assert status_for(findings) is Status.YELLOW


def test_a_government_source_still_sets_red_alongside_news():
    """The asymmetry is the point: news cannot lift a badge to red, and it
    cannot hold one down either."""
    findings = evaluate(
        listing_expiration_date=date(2020, 1, 1),
        news=[NewsItem(headline="All fine, say analysts", url="https://x.test/ok")],
        today=date(2026, 8, 17),
    )
    assert status_for(findings) is Status.RED


def test_the_article_is_attached_so_it_can_be_judged():
    item = NewsItem(
        headline="Blood pressure drug recalled",
        url="https://example.test/story",
        domain="cardiovascularbusiness.com",
        published_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    finding = next(f for f in evaluate(news=[item]) if f.code == "NEWS_SIGNAL")
    assert finding.source_url == "https://example.test/story"
    assert "cardiovascularbusiness.com" in finding.message
    assert "2026-08-12" in finding.message
