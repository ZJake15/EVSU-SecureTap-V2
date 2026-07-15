import io

import numpy as np
import face_recognition
from django.core.files.base import ContentFile
from PIL import Image, ImageOps


def compute_face_encoding(image_file):
    """Returns the 128-d face encoding for the single face in image_file.

    Raises ValueError with a human-readable reason if zero or multiple faces are found,
    so callers (DRF serializers, the bulk-import view) can surface it directly.
    """
    image_file.seek(0)
    # Loading directly via face_recognition.load_image_file() ignores EXIF
    # orientation, so a phone photo that's only "upright" because of its EXIF
    # tag (very common) gets read sideways/upside-down and the detector finds
    # no face. exif_transpose() bakes the correct orientation into the pixels
    # before detection.
    pil_image = ImageOps.exif_transpose(Image.open(image_file))
    image = np.array(pil_image.convert("RGB"))

    encodings = face_recognition.face_encodings(image)
    if not encodings:
        raise ValueError("No face could be detected in the photo. Use a clear, front-facing photo.")
    if len(encodings) > 1:
        raise ValueError("Multiple faces were detected in the photo. Use a photo with only one person.")
    return encodings[0]


def normalize_to_jpeg(image_file):
    """Re-encodes image_file as a JPEG (applying EXIF rotation first) for
    storage as photo_reference. Uploads can arrive in formats browsers can't
    render at all (HEIC, most notably) - normalizing here means the
    dashboard's <img> thumbnails always work regardless of the original
    upload format."""
    image_file.seek(0)
    pil_image = ImageOps.exif_transpose(Image.open(image_file)).convert("RGB")
    buffer = io.BytesIO()
    pil_image.save(buffer, format="JPEG", quality=90)
    return ContentFile(buffer.getvalue(), name="reference.jpg")
