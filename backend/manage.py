#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "securetap_project.settings")
    # Read when OpenCV loads, which Django does at startup: a command that opens
    # the camera (record_face_motion) needs it set as the gate monitor sets it
    # (entry-agent/camera.py) - with it on, the first frame took 44 s, not 1.
    os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment? (conda activate securetap)"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
