"""Which organ each finding is about.

The assessment already says *what* is wrong and *how much*; this says *where*,
so a body diagram can shade the organs a drug actually bears on for this
patient. It is presentation, not scoring -- nothing here changes a verdict.

Two rules keep it honest, because a shaded organ reads as a clinical claim:

**Only findings map.** The organ set is derived from the findings `assess()`
produced, never from the drug or the patient in general. Warfarin does not
shade a liver because warfarin is hepatically cleared; it shades one when THIS
patient's assessment raised a hepatic finding. Otherwise the picture says
something the ruleset never said.

**Unmapped is visible, not silent.** A finding with no organ is reported as
unmapped rather than dropped. A diagram that quietly omits a finding is worse
than one that admits it does not know where to put it -- the reader counts
organs and believes they have seen everything.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

# The organs the front-end can draw. Kept small and anatomical: this is a body
# diagram, not a systems chart, and an organ nobody can point to on a torso is
# not something a physician reads at a glance.
ORGANS = (
    "brain",
    "thyroid",
    "heart",
    "lungs",
    "oesophagus",
    "liver",
    "gallbladder",
    "spleen",
    "stomach",
    "pancreas",
    "kidneys",
    "intestines",
    "bladder",
    "blood",
    "skin",
)

# The drawable set, for O(1) membership checks when filtering a model's answer
# down to organs the figure actually has an anchor for.
_ORGAN_SET = frozenset(ORGANS)

# Finding code -> organs it bears on. Every code the ruleset can emit appears
# here, including the ones that map to nothing, so a new finding cannot be
# added without someone deciding where it belongs.
FINDING_ORGANS: dict[str, tuple[str, ...]] = {
    # Renal dosing is about the kidney doing the clearing.
    "RENAL_DOSE_EXCEEDED": ("kidneys",),
    # Hepatic impairment is about the liver doing the metabolising.
    "HEPATIC_IMPAIRED": ("liver",),
    # An allergy is systemic. Skin is where it is usually seen and lungs are
    # where it kills, so both are shaded rather than picking the visible one.
    "ALLERGY_MATCH": ("skin", "lungs"),
    # Two drugs of a class stack the same organ effect. Which organ depends on
    # the class, so this resolves through DUPLICATE_CLASS_ORGANS below.
    "DUPLICATE_CLASS": (),
    "DUPLICATE_INGREDIENT": (),
    # An interaction is a pharmacokinetic event, and the liver is where most of
    # them happen. Deliberately not the target organ of either drug: the
    # interaction is about clearance, not about what the drugs treat.
    "INTERACTION_MAJOR": ("liver",),
    "INTERACTION_MODERATE": ("liver",),
    # A narrow index means the gap between dose and toxicity is small. That is a
    # property of the drug, not of one organ.
    # Which organ depends entirely on which condition and which class -- a
    # steroid in diabetes is pancreatic, a beta blocker in asthma is pulmonary.
    # The pair is in the message, not in a structured field, so there is nothing
    # here to map from. Unmapped and reported, rather than guessed.
    "CONDITION_WORSENED": (),
    # A coverage gap, not a harm. The organ is not in doubt -- RENAL_UNKNOWN is
    # about kidneys and HEPATIC_UNKNOWN about the liver -- so the figure marks
    # it, and the INFO severity is what makes it read as "not assessed" rather
    # than "impacted". Leaving them unmapped drew nothing at all, which told a
    # prescriber the organ was fine when the truth was that nobody had checked.
    "RENAL_UNKNOWN": ("kidneys",),
    "HEPATIC_UNKNOWN": ("liver",),
    "NARROW_THERAPEUTIC_INDEX": (),
    # Age-inappropriate prescribing in the Beers sense is mostly anticholinergic
    # and sedative burden -- falls and confusion.
    "AGE_INAPPROPRIATE": ("brain",),
    # A prior reaction to the class says nothing about which organ reacted; the
    # reaction text would, and we do not store it structured.
    "PRIOR_ADR_SAME_CLASS": (),
    # Population signals name a reaction, so they resolve through REACTION_ORGANS.
    "ADR_SIGNAL": (),
    "ADR_SIGNAL_STRONG": (),
    # Metabolism is the liver, whatever the gene.
    "PGX_ACTIONABLE": ("liver",),
    "PGX_INFORMATIVE": ("liver",),
    "DRUG_CLASS_UNKNOWN": (),
}

# Drug class -> the organ a duplicate of that class stacks on.
DUPLICATE_CLASS_ORGANS: dict[str, tuple[str, ...]] = {
    "nsaid": ("stomach", "kidneys", "oesophagus"),
    "anticoagulant": ("blood",),
    "benzodiazepine": ("brain",),
    "opioid": ("brain", "lungs"),
    "ssri": ("brain",),
    "statin": ("liver",),
    "ace_inhibitor": ("kidneys",),
    "arb": ("kidneys",),
    "loop_diuretic": ("kidneys", "bladder"),
    "thiazide": ("kidneys",),
    "potassium_sparing": ("kidneys",),
    "ppi": ("stomach", "kidneys"),
    "h2_blocker": ("stomach",),
    "gabapentinoid": ("brain",),
    "z_drug": ("brain",),
    "antipsychotic": ("brain", "heart"),
    "tricyclic": ("brain", "heart"),
    "snri": ("brain",),
    "antiplatelet": ("blood",),
    "calcium_blocker": ("heart",),
    "antiarrhythmic": ("heart", "thyroid", "lungs"),
    "digoxin": ("heart",),
    "lithium": ("kidneys", "thyroid"),
    "corticosteroid": ("stomach", "pancreas"),
    "hypoglycaemic": ("pancreas",),
    "fluoroquinolone": ("brain",),
    "aminoglycoside": ("kidneys",),
    "glycopeptide": ("kidneys",),
    "macrolide": ("heart",),
    "antimetabolite": ("liver", "blood"),
    "paracetamol": ("liver",),
    "beta_blocker": ("heart",),
    "diuretic": ("kidneys", "bladder"),
    "bisphosphonate": ("oesophagus",),
    "anticholinergic": ("bladder", "brain"),
    "thyroid_hormone": ("thyroid", "heart"),
    # The class key is "biguanide", not "metformin" -- this entry was written
    # against the drug name and never matched, so a doubled metformin shaded
    # nothing. Named after the class the ruleset actually assigns.
    "biguanide": ("kidneys",),
}

# Words in a reaction name -> the organ it happens in. Matched on substrings
# because the source is FAERS and label prose, where the same event is written
# a dozen ways. Ordered most specific first: "hepatic failure" must reach the
# liver before "failure" reaches the heart.
REACTION_ORGANS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pancreatit", ("pancreas",)),
    ("pancrea", ("pancreas",)),
    ("cholestas", ("liver", "gallbladder")),
    ("cholecystit", ("gallbladder",)),
    ("cholelith", ("gallbladder",)),
    ("biliary", ("gallbladder", "liver")),
    ("thyroid", ("thyroid",)),
    ("hypothyroid", ("thyroid",)),
    ("hyperthyroid", ("thyroid",)),
    ("oesophag", ("oesophagus",)),
    ("esophag", ("oesophagus",)),
    ("dysphagia", ("oesophagus",)),
    ("cystit", ("bladder",)),
    ("urinary retention", ("bladder",)),
    ("dysuria", ("bladder",)),
    ("haematuria", ("bladder", "kidneys")),
    ("splenomegal", ("spleen",)),
    ("hepat", ("liver",)),
    ("liver", ("liver",)),
    ("cirrho", ("liver",)),
    ("jaundice", ("liver",)),
    ("renal", ("kidneys",)),
    ("kidney", ("kidneys",)),
    ("nephro", ("kidneys",)),
    ("cardiac", ("heart",)),
    ("myocard", ("heart",)),
    ("qt", ("heart",)),
    ("arrhythm", ("heart",)),
    ("bradycard", ("heart",)),
    ("tachycard", ("heart",)),
    ("respirat", ("lungs",)),
    ("pulmonar", ("lungs",)),
    ("pneumon", ("lungs",)),
    ("bronch", ("lungs",)),
    ("gastro", ("stomach",)),
    ("gastric", ("stomach",)),
    ("ulcer", ("stomach",)),
    ("nausea", ("stomach",)),
    ("vomit", ("stomach",)),
    ("diarrh", ("intestines",)),
    ("colitis", ("intestines",)),
    ("constipat", ("intestines",)),
    ("haemorrhage", ("blood",)),
    ("hemorrhage", ("blood",)),
    ("bleed", ("blood",)),
    ("anaemia", ("blood",)),
    ("anemia", ("blood",)),
    ("thrombocytopen", ("blood",)),
    ("neutropen", ("blood",)),
    ("agranulocyt", ("blood",)),
    ("acidosis", ("blood", "kidneys")),
    ("seizure", ("brain",)),
    ("convulsion", ("brain",)),
    ("confusion", ("brain",)),
    ("somnolence", ("brain",)),
    ("dizzi", ("brain",)),
    ("headache", ("brain",)),
    ("serotonin syndrome", ("brain",)),
    ("rash", ("skin",)),
    ("urticaria", ("skin",)),
    ("pruritus", ("skin",)),
    ("stevens-johnson", ("skin",)),
    ("angioedema", ("skin", "lungs")),
    ("anaphyla", ("skin", "lungs")),
)


# Avoided-ingredient advisories name an ingredient, not a reaction, and the
# organ they bear on is the ingredient's, not the drug's. Matched on the
# ingredient name the finding message carries -- the same message-driven
# resolution the population signals use, kept separate because the vocabulary is
# ingredients (what the profile flags), not reaction words. Add a line here when
# an ingredient is added to AVOID_INGREDIENT_CODES; an ingredient with no entry
# is reported unmapped rather than shading a guessed organ.
INGREDIENT_ORGANS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # A stimulant: palpitations and tachycardia in the heart, tremor and
    # sleeplessness in the brain.
    ("caffeine", ("heart", "brain")),
)


@dataclass(frozen=True)
class OrganImpact:
    """One organ, and why it is shaded."""

    organ: str
    severity: str            # block | high | moderate | low
    weight: int              # summed weight of the findings that put it here
    reasons: tuple[str, ...]  # the finding messages, for the tooltip

    def as_dict(self) -> dict:
        return {
            "organ": self.organ,
            "severity": self.severity,
            "weight": self.weight,
            "reasons": list(self.reasons),
        }


# Worst wins when several findings land on one organ. A kidney carrying a block
# and a nudge is a blocked kidney.
_SEVERITY_RANK = {"block": 0, "high": 1, "moderate": 2, "low": 3}


def organs_for_reaction(reaction: str) -> tuple[str, ...]:
    """Where a named adverse reaction happens, or `()` if we cannot tell.

    Substring matching on purpose: FAERS and label prose write the same event
    many ways, and an exact-match table would silently cover almost nothing.
    """
    text = (reaction or "").casefold()
    hits: list[str] = []
    for needle, organs in REACTION_ORGANS:
        if needle in text:
            hits.extend(o for o in organs if o not in hits)
    return tuple(hits)


def organs_for_ingredient(text: str) -> tuple[str, ...]:
    """Where an avoided ingredient bears on the body, or `()` if we cannot tell.

    Substring matching on the ingredient name the finding message carries, the
    same way `organs_for_reaction` reads a reaction name -- the avoid list is
    small and its names are exact, but a substring keeps 'caffeine' matching
    'caffeine citrate' without a second table.
    """
    haystack = (text or "").casefold()
    hits: list[str] = []
    for needle, organs in INGREDIENT_ORGANS:
        if needle in haystack:
            hits.extend(o for o in organs if o not in hits)
    return tuple(hits)


def organs_for_finding(
    code: str, message: str = "", drug_class: str | None = None
) -> tuple[str, ...]:
    """Where one finding bears on the body.

    `message` is used only for the codes whose organ genuinely depends on what
    the message names -- the population signals (a reaction) and the avoided
    ingredient (an ingredient). Reading it for the rest would let a phrase in a
    sentence shade an organ the ruleset never implicated.
    """
    if code in ("ADR_SIGNAL", "ADR_SIGNAL_STRONG"):
        return organs_for_reaction(message)
    if code == "AVOIDED_INGREDIENT":
        return organs_for_ingredient(message)
    if code == "DUPLICATE_CLASS" and drug_class:
        return DUPLICATE_CLASS_ORGANS.get(drug_class.casefold(), ())
    return FINDING_ORGANS.get(code, ())


# Findings whose organ is carried in their message as prose -- a reaction name
# or an avoided ingredient. When the substring tables miss the phrase, these are
# the only codes the LLM fallback is allowed to place: they describe a concrete
# effect on the body. A code deliberately mapped to "no single organ"
# (NARROW_THERAPEUTIC_INDEX, CONDITION_WORSENED, ...) is a design decision, not a
# gap, so it is never sent to the model.
_LLM_PLACEABLE_CODES = frozenset({"ADR_SIGNAL", "ADR_SIGNAL_STRONG", "AVOIDED_INGREDIENT"})


def impacts(
    findings: Iterable,
    drug_class: str | None = None,
    *,
    infer_organs: Callable[[str], Sequence[str]] | None = None,
) -> tuple[list[OrganImpact], list[str]]:
    """Turn an assessment's findings into shaded organs.

    Returns `(impacts, unmapped)`. `unmapped` names the finding codes that
    belong nowhere on the diagram, and the caller is expected to show it: a
    picture that quietly omits a finding invites the reader to believe the
    organs are the whole story.

    `infer_organs` is an optional model-backed fallback (organ_infer.py): when
    the substring tables cannot place a prose-carrying effect (a rare reaction,
    an unlisted ingredient), it is asked where the effect acts. It is only
    consulted for `_LLM_PLACEABLE_CODES`, and only after the deterministic
    lookup comes back empty, so it fills gaps and never overrides a rule or a
    deliberate "no single organ" decision. An empty answer leaves the finding
    unmapped, exactly as without it.
    """
    collected: dict[str, dict] = {}
    unmapped: list[str] = []

    for finding in findings:
        code = getattr(finding, "code", "") or ""
        message = getattr(finding, "message", "") or ""
        weight = int(getattr(finding, "weight", 0) or 0)
        severity = str(getattr(getattr(finding, "severity", None), "value", "") or "low")

        organs = organs_for_finding(code, message, drug_class)
        if not organs and infer_organs is not None and code in _LLM_PLACEABLE_CODES and message:
            organs = tuple(o for o in infer_organs(message) if o in _ORGAN_SET)
        if not organs:
            if code not in unmapped:
                unmapped.append(code)
            continue

        for organ in organs:
            entry = collected.setdefault(
                organ, {"weight": 0, "severity": severity, "reasons": []}
            )
            entry["weight"] += weight
            if _SEVERITY_RANK.get(severity, 9) < _SEVERITY_RANK.get(entry["severity"], 9):
                entry["severity"] = severity
            if message and message not in entry["reasons"]:
                entry["reasons"].append(message)

    out = [
        OrganImpact(organ, e["severity"], e["weight"], tuple(e["reasons"]))
        for organ, e in collected.items()
    ]
    # Heaviest first: the diagram's legend reads top-down and the organ that
    # drove the verdict should be the first one named.
    out.sort(key=lambda i: (_SEVERITY_RANK.get(i.severity, 9), -i.weight))
    return out, unmapped


def compare(
    current: Sequence, candidate: Sequence, drug_class: str | None = None
) -> dict:
    """Two assessments, side by side, for the analogue view.

    The question a physician asks of a substitute is not "is this drug safe"
    but "does it move the problem". So this reports which organs the swap
    relieves, which it newly burdens, and which it leaves exactly as they were
    -- the last being the honest answer that a substitution often is not an
    improvement.
    """
    now, now_unmapped = impacts(current, drug_class)
    then, then_unmapped = impacts(candidate, drug_class)
    now_by = {i.organ: i for i in now}
    then_by = {i.organ: i for i in then}

    return {
        "current": [i.as_dict() for i in now],
        "candidate": [i.as_dict() for i in then],
        "relieved": sorted(set(now_by) - set(then_by)),
        "introduced": sorted(set(then_by) - set(now_by)),
        "unchanged": sorted(set(now_by) & set(then_by)),
        "unmapped": sorted(set(now_unmapped) | set(then_unmapped)),
    }
