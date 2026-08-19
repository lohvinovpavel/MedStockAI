"""Which NDCs a certification pass covers.

Certification used to run against the shelf alone, which meant a drug had to be
in stock before anyone could find out whether it had been recalled. That is
backwards for the surface it feeds: substitution proposes drugs the hospital
does *not* hold, so the badge on the candidate you were about to switch to was
the one guaranteed to read Unknown.

The failure was invisible in every existing test because they all exercise the
field mapping, which was never wrong -- the mapping is only ever handed products
that came back, and the bug was in which NDCs were asked for.

DB-free, like the rest of this module's tests: the two sources are stubbed and
what is asserted is the composition, which is where the defect lived.
"""

from app import certification


def _targets(monkeypatch, catalogue, shelf):
    monkeypatch.setattr(certification, "catalogue_ndcs", lambda: list(catalogue))
    monkeypatch.setattr(certification, "shelf_ndcs", lambda: list(shelf))
    return certification.certifiable_ndcs()


def test_a_catalogue_drug_is_certified_even_when_nothing_is_in_stock(monkeypatch):
    """The regression this guards. An empty shelf must not mean an empty pass:
    a prescribable drug is a certifiable drug whether or not it is held."""
    assert _targets(monkeypatch, ["00093922201"], []) == ["00093922201"]


def test_a_stocked_ndc_the_catalogue_does_not_know_is_still_certified(monkeypatch):
    """Stock and catalogue are populated by different jobs, so the shelf can
    carry an NDC `drug` has never seen. Narrowing to the catalogue would take a
    working badge away from something physically on a shelf."""
    assert _targets(monkeypatch, [], ["00378964532"]) == ["00378964532"]


def test_overlap_is_asked_for_once(monkeypatch):
    """Every duplicate is an OR-term in a batched openFDA query, and the batches
    are sized on that count."""
    assert _targets(
        monkeypatch, ["00093922201", "00031930101"], ["00093922201"]
    ) == ["00031930101", "00093922201"]


def test_targets_are_sorted(monkeypatch):
    """Ordering decides how NDCs fall into batches. Stable order keeps a
    re-run's request pattern reproducible, which is what makes a 404 for one
    chunk diagnosable."""
    assert _targets(monkeypatch, ["00093922205", "00031930101"], ["00093922201"]) == [
        "00031930101",
        "00093922201",
        "00093922205",
    ]
