"""Mastery is an evidence-weighted heuristic, not a calibrated probability."""
from .memory import get_long_term_memory, update_long_term_memory


def get_student_model(db, user_id: int) -> dict:
    return get_long_term_memory(db, user_id)


def update_mastery(db, user_id: int, concept: str, score: float, evidence_id: int,
                   verified: bool = False) -> dict:
    data = get_student_model(db, user_id)
    mastery = data['mastery']
    old = mastery.get(concept, .5)
    weight = .2 if verified else .04
    mastery[concept] = round(max(0, min(1, (1 - weight) * old + weight * score)), 4)
    return update_long_term_memory(db, user_id, {'mastery': mastery}, evidence_id=evidence_id)


def update_misconceptions(db, user_id: int, concept: str, hypotheses: list[str], evidence_id: int):
    data = get_student_model(db, user_id)
    entries = data['misconceptions']
    for hypothesis in hypotheses[:3]:
        if not any(m['hypothesis'] == hypothesis and m['concept'] == concept for m in entries):
            entries.append({'concept': concept, 'hypothesis': hypothesis,
                            'evidence_event_id': evidence_id, 'status': 'unconfirmed'})
    return update_long_term_memory(db, user_id, {'misconceptions': entries[-30:]}, evidence_id=evidence_id)
