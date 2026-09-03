import numpy as np
from django.conf import settings

from .models import ConfusablePair, Person

# Extra headroom for a person already flagged in a confusable pair - more
# guided photos give the matcher more of their actual variation to compare
# against, which is the only thing that can help here (see the module-level
# discussion in this brief: matching accuracy itself is not fixable for two
# genuinely similar faces, but every embedding on file still improves *this
# system's* chance of landing on the highest similarity it's capable of
# producing for the RIGHT person specifically, which matters once a forced
# card tap enters the picture too). Confusable pairs should be rare, so the
# extra per-frame comparison cost across the whole enrolled population stays
# negligible - this is deliberately NOT a raise to MAX_EMBEDDINGS_PER_PERSON
# (see users/serializers.py) for everyone.
MAX_EMBEDDINGS_PER_CONFUSABLE_PERSON = 8


def find_confusable_candidates(new_embedding, exclude_person_id):
    """Compares one new embedding against every OTHER active person's
    embeddings - same vectorized idea IdentifyView uses for gate-scan
    matching, just run once at enrollment time instead of per camera frame.
    Returns [(person, best_similarity), ...] for every person whose best
    cross-similarity against this embedding clears
    settings.CONFUSABLE_SIMILARITY_THRESHOLD, highest similarity first.

    Only active people are compared against - a deactivated person can't be
    scanned at the gate, so a pair involving one doesn't need to keep
    triggering the forced-tiebreak protection in real time. If they're later
    reactivated, the next photo added to either record will detect the pair
    again (already-recorded pairs are never deleted by deactivation)."""
    query = np.array(new_embedding, dtype=np.float32)
    matches = []
    candidates = Person.objects.filter(is_active=True).exclude(id=exclude_person_id).prefetch_related(
        "face_embeddings"
    )
    for person in candidates:
        embeddings = person.face_embeddings.all()  # prefetched, no extra query
        if not embeddings:
            continue
        best_similarity = max(
            float(np.dot(np.array(face_embedding.embedding, dtype=np.float32), query))
            for face_embedding in embeddings
        )
        if best_similarity >= settings.CONFUSABLE_SIMILARITY_THRESHOLD:
            matches.append((person, best_similarity))
    matches.sort(key=lambda pair: pair[1], reverse=True)
    return matches


def record_confusable_pairs(person, matches):
    """Persists every detected match as a ConfusablePair (idempotent - a
    pair that's already on record isn't duplicated, and its original
    detected_similarity is left alone rather than overwritten by a later,
    possibly different reading) and returns a plain-dict summary for
    whichever enrollment endpoint called this, to surface as a warning."""
    warnings = []
    for other_person, similarity in matches:
        ConfusablePair.objects.get_or_create_pair(
            person, other_person, source=ConfusablePair.Source.AUTO_DETECTED, similarity=similarity,
        )
        warnings.append(
            {
                "person_id": other_person.id,
                "full_name": other_person.full_name,
                "student_or_employee_id": other_person.student_or_employee_id,
                "similarity": similarity,
            }
        )
    return warnings


def get_confusable_partner_ids(person_id):
    return ConfusablePair.objects.partner_ids_for(person_id)


def has_confusable_flag(person_id):
    return bool(get_confusable_partner_ids(person_id))
