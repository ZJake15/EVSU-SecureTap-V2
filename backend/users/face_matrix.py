"""The gate's comparison table of enrolled faces, kept in memory.

IdentifyView compares every face it sees against every active person's face
embeddings. Building that table means reading every FaceEmbedding row and
turning each stored JSON list of 512 numbers back into numbers - measured,
about 40 ms for 50 people and 0.8 s for 1,000, on every frame from every
camera - while the comparison itself takes well under a millisecond. So the
table is built once and reused until the enrolled faces change.

How a change is noticed:
- Saving or deleting a Person or FaceEmbedding in this process (the
  dashboard, enrollment, deactivation approval) marks the table stale as
  soon as that change is committed (connect(), via signals).
- Every call also runs one cheap query - how many active people's
  embeddings there are, the highest id, the sum of their owners' ids - so a
  change that skips signals (a queryset .update(), a bulk import, another
  process such as a management command) is still caught on the next frame.
- An embedding rewritten in place by another process (recompute_embeddings)
  keeps the same count and ids, so the table is also rebuilt at least every
  MAX_AGE_SECONDS regardless.

Holds person ids only, never Person objects: who a match is gets read fresh
from the database for that one person (IdentifyView._match_one_face), so a
name or photo edited a moment ago shows at once.
"""

import threading
import time

import numpy as np
from django.db import transaction
from django.db.models import Count, Max, Sum
from django.db.models.signals import post_delete, post_save

MAX_AGE_SECONDS = 300

_lock = threading.Lock()
_state = {
    "generation": 0,  # bumped by every committed Person/FaceEmbedding change
    "built_generation": -1,
    "fingerprint": None,
    "built_at": 0.0,
    "matrix": None,
    "owner_ids": None,
}


def _fingerprint():
    from .models import FaceEmbedding

    summary = FaceEmbedding.objects.filter(person__is_active=True).aggregate(
        rows=Count("id"), newest=Max("id"), owners=Sum("person_id"),
    )
    return summary["rows"], summary["newest"], summary["owners"]


def _build():
    from .models import FaceEmbedding

    rows = list(
        FaceEmbedding.objects.filter(person__is_active=True).order_by("id").values_list("person_id", "embedding")
    )
    if not rows:
        return None, None
    owner_ids = np.fromiter((person_id for person_id, _embedding in rows), dtype=np.int64, count=len(rows))
    matrix = np.array([embedding for _person_id, embedding in rows], dtype=np.float32)
    return matrix, owner_ids


def current():
    """(matrix, owner_ids): an (N, 512) float32 matrix with one row per
    embedding of every active person, and the id of the person who owns each
    row - or (None, None) when nobody active has a face enrolled. Shared
    between requests: read it, never modify it."""
    fingerprint = _fingerprint()
    now = time.monotonic()
    with _lock:
        fresh = (
            _state["built_generation"] == _state["generation"]
            and _state["fingerprint"] == fingerprint
            and now - _state["built_at"] < MAX_AGE_SECONDS
        )
        if not fresh:
            generation = _state["generation"]
            matrix, owner_ids = _build()
            _state.update(
                built_generation=generation, fingerprint=fingerprint, built_at=now,
                matrix=matrix, owner_ids=owner_ids,
            )
        return _state["matrix"], _state["owner_ids"]


def _mark_stale():
    with _lock:
        _state["generation"] += 1


def _on_change(sender, **kwargs):
    # After the commit, not at save time: a table rebuilt between the two
    # would read the old rows and still count as up to date.
    transaction.on_commit(_mark_stale)


def connect():
    """Called once from UsersConfig.ready()."""
    from .models import FaceEmbedding, Person

    for model in (Person, FaceEmbedding):
        post_save.connect(_on_change, sender=model, dispatch_uid=f"face_matrix_save_{model.__name__}")
        post_delete.connect(_on_change, sender=model, dispatch_uid=f"face_matrix_delete_{model.__name__}")
