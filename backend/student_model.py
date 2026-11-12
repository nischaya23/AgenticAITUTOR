"""Student model: per-concept mastery via Bayesian Knowledge Tracing (BKT).

Each concept stores P(L), the probability that the student has learned it.
After every graded attempt we (1) apply Bayes' rule to the observation and
(2) apply the learning transition. Because LLM grades are partial (0..1) and
noisier than verified checks, the score is treated as soft evidence and
unverified grading uses higher slip/guess rates.

Corbett & Anderson (1995), "Knowledge tracing: Modeling the acquisition of
procedural knowledge". Parameters below are hand-set defaults, not fitted.
"""
from dataclasses import dataclass
from .memory import get_long_term_memory, update_long_term_memory


@dataclass(frozen=True)
class BKTParams:
    p_init: float     # P(L0): prior that a new concept is already known
    p_transit: float  # P(T): chance of learning the concept on each attempt
    p_slip: float     # P(S): knows it but answers wrong
    p_guess: float    # P(G): doesn't know it but answers right


VERIFIED = BKTParams(p_init=.3, p_transit=.1, p_slip=.1, p_guess=.2)
# LLM grading is provisional, so its observations are treated as less reliable.
LLM = BKTParams(p_init=.3, p_transit=.1, p_slip=.15, p_guess=.25)
P_INIT = VERIFIED.p_init


def bkt_update(prior: float | None, score: float, verified: bool = False) -> float:
    """Return the new P(L) after one attempt with a score in [0, 1]."""
    p = LLM if not verified else VERIFIED
    known = P_INIT if prior is None else min(max(prior, 0.), 1.)
    score = min(max(score, 0.), 1.)
    # Posterior P(L | correct) and P(L | wrong).
    if_correct = known * (1 - p.p_slip) / (known * (1 - p.p_slip) + (1 - known) * p.p_guess)
    if_wrong = known * p.p_slip / (known * p.p_slip + (1 - known) * (1 - p.p_guess))
    # Partial credit: mix the two posteriors by the score (soft evidence).
    posterior = score * if_correct + (1 - score) * if_wrong
    # Learning transition: the student may have learned it during this attempt.
    return posterior + (1 - posterior) * p.p_transit


def get_student_model(db, user_id: int) -> dict:
    return get_long_term_memory(db, user_id)


def update_mastery(db, user_id: int, concept: str, score: float, evidence_id: int,
                   verified: bool = False) -> dict:
    data = get_student_model(db, user_id)
    mastery = data['mastery']
    mastery[concept] = round(bkt_update(mastery.get(concept), score, verified), 4)
    return update_long_term_memory(db, user_id, {'mastery': mastery}, evidence_id=evidence_id)


def update_misconceptions(db, user_id: int, concept: str, hypotheses: list[str], evidence_id: int):
    data = get_student_model(db, user_id)
    entries = data['misconceptions']
    for hypothesis in hypotheses[:3]:
        if not any(m['hypothesis'] == hypothesis and m['concept'] == concept for m in entries):
            entries.append({'concept': concept, 'hypothesis': hypothesis,
                            'evidence_event_id': evidence_id, 'status': 'unconfirmed'})
    return update_long_term_memory(db, user_id, {'misconceptions': entries[-30:]}, evidence_id=evidence_id)
