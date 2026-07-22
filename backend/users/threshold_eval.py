import numpy as np

from .models import Person

# Shared by the evaluate_threshold management command and the dashboard's
# /api/reports/far-frr endpoint, so the two never drift apart.
DEFAULT_THRESHOLD_SWEEP = np.arange(0.10, 0.85, 0.05)


def leave_one_out_similarities():
    """Compares every enrolled embedding against every other one - same
    person -> a false-reject signal, different person -> a false-accept
    signal. Returns (same_person_similarities, cross_person_similarities),
    both plain lists of floats."""
    people = list(Person.objects.filter(is_active=True).prefetch_related("face_embeddings"))
    rows = []  # (person_id, embedding_array)
    for person in people:
        for face_embedding in person.face_embeddings.all():
            rows.append((person.id, np.array(face_embedding.embedding, dtype=np.float32)))

    same_person, cross_person = [], []
    for i, (person_id_a, vector_a) in enumerate(rows):
        for person_id_b, vector_b in rows[i + 1 :]:
            similarity = float(np.dot(vector_a, vector_b))
            if person_id_a == person_id_b:
                same_person.append(similarity)
            else:
                cross_person.append(similarity)
    return same_person, cross_person


def far_frr_table(same_person_similarities, cross_person_similarities, thresholds=DEFAULT_THRESHOLD_SWEEP):
    """FAR = fraction of DIFFERENT people wrongly accepted as a match at that
    threshold (lower is safer). FRR = fraction of the SAME person wrongly
    rejected (lower is more convenient). Returns a list of
    {"threshold", "far", "frr"} dicts, one per threshold in the sweep."""
    same = np.array(same_person_similarities)
    cross = np.array(cross_person_similarities)

    rows = []
    for threshold in thresholds:
        far = float(np.mean(cross >= threshold)) if cross.size else None
        frr = float(np.mean(same < threshold)) if same.size else None
        rows.append({"threshold": round(float(threshold), 2), "far": far, "frr": frr})
    return rows
