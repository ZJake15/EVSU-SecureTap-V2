"""Photo files follow their records.

Django deletes a database row but leaves the photo file it pointed at on
disk - so permanently deleting a person, replacing their photo, or deleting
an old gate record used to leave face photos behind in the media folder that
nothing in the system could show or delete any more. These hooks remove the
file too:

- a record with a photo is deleted (a person, their face data, a gate
  record) -> its photo file is deleted;
- a photo is replaced by a new one -> the old file is deleted.

A file is only removed once no record anywhere still refers to it, and only
after the database change has actually been saved (a rolled-back delete
keeps its file). Loading data (`loaddata`, the import) never deletes files.

orphaned_media_files() finds photos already left behind before this
existed; the daily clean-up (purge_old_data) removes them.
"""

import time
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save, pre_save

# Files younger than this are never treated as left behind - one may be
# uploaded a moment before the record that points at it is saved.
ORPHAN_MIN_AGE_SECONDS = 24 * 60 * 60


def _file_fields(model):
    return [field for field in model._meta.get_fields() if isinstance(field, models.FileField)]


def _photo_models():
    return [model for model in apps.get_models() if _file_fields(model)]


def referenced_names():
    """Every file name any record in the database points at."""
    names = set()
    for model in _photo_models():
        for field in _file_fields(model):
            names.update(model._default_manager.exclude(**{field.name: ""})
                         .exclude(**{f"{field.name}__isnull": True}).values_list(field.name, flat=True))
    return names


def _still_referenced(name):
    for model in _photo_models():
        for field in _file_fields(model):
            if model._default_manager.filter(**{field.name: name}).exists():
                return True
    return False


def _delete_after_commit(storage, name):
    """Deletes the file once the surrounding database change is saved -
    unless some other record still uses the same file."""
    if not name:
        return

    def delete():
        if not _still_referenced(name):
            storage.delete(name)

    transaction.on_commit(delete)


def _on_delete(sender, instance, **kwargs):
    for field in _file_fields(sender):
        field_file = getattr(instance, field.name)
        if field_file:
            _delete_after_commit(field_file.storage, field_file.name)


def _remember_old_files(sender, instance, raw=False, **kwargs):
    """Before a save: note the file names the record had, so the save can
    tell which were replaced."""
    instance._photo_files_before = {}
    if raw or instance.pk is None:
        return  # loading data, or a brand-new record - nothing is being replaced
    fields = _file_fields(sender)
    old = sender._default_manager.filter(pk=instance.pk).values(*[field.name for field in fields]).first()
    if old:
        instance._photo_files_before = {field.name: old[field.name] for field in fields}


def _delete_replaced_files(sender, instance, raw=False, **kwargs):
    """After a save: delete each file the record no longer points at."""
    before = getattr(instance, "_photo_files_before", {})
    if raw or not before:
        return
    for field in _file_fields(sender):
        old_name = before.get(field.name)
        new_file = getattr(instance, field.name)
        if old_name and old_name != (new_file.name if new_file else None):
            _delete_after_commit(field.storage, old_name)


def connect():
    """Called from UsersConfig.ready(), once every app's models are loaded."""
    for model in _photo_models():
        uid = model._meta.label
        post_delete.connect(_on_delete, sender=model, dispatch_uid=f"photo_files_delete_{uid}")
        pre_save.connect(_remember_old_files, sender=model, dispatch_uid=f"photo_files_before_{uid}")
        post_save.connect(_delete_replaced_files, sender=model, dispatch_uid=f"photo_files_after_{uid}")


def orphaned_media_files(min_age_seconds=ORPHAN_MIN_AGE_SECONDS):
    """Files under MEDIA_ROOT that no record points at any more (left behind
    before these hooks existed), older than min_age_seconds. Paths."""
    root = Path(settings.MEDIA_ROOT)
    if not root.is_dir():
        return []
    referenced = referenced_names()
    cutoff = time.time() - min_age_seconds
    return [
        path for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in referenced and path.stat().st_mtime < cutoff
    ]
