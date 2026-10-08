"""How fast can this computer run the gate's face scan?

    python manage.py benchmark_scan                 # 30 frames, today's settings
    python manage.py benchmark_scan --det-size 384  # try a smaller detection size
    python manage.py benchmark_scan --cores 4       # pretend this PC only has 4 cores
    python manage.py benchmark_scan --sample --json # the launcher's speed test

Runs the same heavy steps the gate does for every camera frame - find the
faces, make each face's fingerprint, run the fake-face check - on one
enrolled photo, many times, and reports the time per frame and the memory
used. Nothing is written to the database. Run it on the presentation laptop
itself to see its real numbers.

--sample uses a public test photo that comes with InsightFace instead of an
enrolled one, so it works before anyone is registered and gives the same
picture on every computer - that's what the launcher's speed test runs.
"""

import ctypes
import json
import os
import statistics
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import insightface
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from users import insightface_utils, liveness_utils
from users.models import FaceEmbedding

# A group photo shipped inside the InsightFace package (public test data).
SAMPLE_PHOTO = Path(insightface.__file__).resolve().parent / "data" / "images" / "t1.jpg"


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _memory_mb():
    """This process's memory in use (MB), or None off Windows."""
    try:
        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(_MemoryCounters)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return counters.WorkingSetSize / 1048576
    except Exception:
        return None


def _limit_cores(cores):
    """Keeps this process on `cores` CPU cores (every other logical CPU, so
    each is a separate physical core on a hyper-threaded chip)."""
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    mask = sum(1 << (2 * i) for i in range(cores))
    kernel32.SetProcessAffinityMask(kernel32.GetCurrentProcess(), mask)


def _sample_frame():
    """A 4:3 camera-shaped frame cut from the sample photo around its largest
    face, the face about 40% of the frame's width - roughly one person
    standing in front of the gate camera, and tight enough to leave the
    photo's other faces out. Only the cut is returned; the caller scales it
    like any other frame."""
    photo = cv2.imread(str(SAMPLE_PHOTO))
    if photo is None:
        raise CommandError(f"InsightFace's test photo is missing ({SAMPLE_PHOTO}).")
    detections = insightface_utils.compute_face_embeddings_and_boxes(photo)
    if not detections:
        raise CommandError("No face found in InsightFace's test photo.")
    x1, y1, x2, y2 = max((d[1] for d in detections), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    height, width = photo.shape[:2]
    cut_width = min(width, round(2.4 * (x2 - x1)))
    cut_height = min(height, round(cut_width * 3 / 4))
    left = min(max(0, (x1 + x2) // 2 - cut_width // 2), width - cut_width)
    top = min(max(0, (y1 + y2) // 2 - cut_height // 2), height - cut_height)
    return photo[top:top + cut_height, left:left + cut_width].copy()


def _enrolled_frame():
    sample = FaceEmbedding.objects.exclude(source_image="").exclude(source_image__isnull=True).first()
    if sample is None:
        return None, None
    with sample.source_image.open("rb") as fh:
        data = np.frombuffer(fh.read(), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR), sample


class Command(BaseCommand):
    help = "Times the gate's per-frame face scan on this computer."

    def add_arguments(self, parser):
        parser.add_argument("--frames", type=int, default=30, help="How many frames to time (default 30).")
        parser.add_argument("--width", type=int, default=640,
                            help="Frame width sent by the gate monitor, in pixels (default 640).")
        parser.add_argument("--det-size", type=int, default=None,
                            help="Face-detection size to test (default: GATE_SCAN_DET_SIZE from .env).")
        parser.add_argument("--cores", type=int, default=None,
                            help="Only use this many CPU cores, to imitate a smaller laptop.")
        parser.add_argument("--sample", action="store_true",
                            help="Use InsightFace's public test photo instead of an enrolled one.")
        parser.add_argument("--json", action="store_true",
                            help="Print the results as one line of JSON (for the launcher's speed test).")

    def handle(self, *args, **options):
        if options["cores"]:
            _limit_cores(options["cores"])
        if options["det_size"]:
            settings.GATE_SCAN_DET_SIZE = options["det_size"]

        memory_start = _memory_mb()
        load_start = time.perf_counter()
        frame, sample = (None, None) if options["sample"] else _enrolled_frame()
        if frame is None:
            if not options["sample"] and not options["json"]:
                self.stdout.write("No enrolled photo yet - using InsightFace's test photo instead.")
            frame = _sample_frame()  # also loads the gate's face models
        height, width = frame.shape[:2]
        if width != options["width"]:
            # Up or down to the gate monitor's frame width - the sample cut can
            # come out smaller than a real camera frame.
            scale = options["width"] / width
            frame = cv2.resize(frame, (options["width"], round(height * scale)), interpolation=cv2.INTER_AREA)

        insightface_utils.compute_face_embeddings_and_boxes(frame)  # loads the gate's face models
        if sample is not None:
            with sample.source_image.open("rb") as fh:
                try:
                    insightface_utils.compute_face_embedding(fh)  # loads what enrollment uses
                except ValueError:
                    pass  # a photo failing enrollment's quality bar doesn't matter here
        load_seconds = time.perf_counter() - load_start

        timings, faces = [], 0
        # Two unmeasured frames first: the first runs after loading are slower
        # while memory and caches settle, and would skew a short test.
        for index in range(options["frames"] + 2):
            start = time.perf_counter()
            detections = insightface_utils.compute_face_embeddings_and_boxes(frame)
            for detection in detections:
                liveness_utils.compute_liveness_score(frame, detection[1])
            if index >= 2:
                timings.append((time.perf_counter() - start) * 1000)
            faces = len(detections)
        memory_end = _memory_mb()

        average = statistics.mean(timings)
        slowest = sorted(timings)[int(len(timings) * 0.95) - 1] if len(timings) > 1 else timings[0]
        if options["json"]:
            self.stdout.write(json.dumps({
                "ms_per_frame": round(average, 1), "ms_median": round(statistics.median(timings), 1),
                "ms_95": round(slowest, 1),
                "load_seconds": round(load_seconds, 1), "faces": faces,
                "det_size": settings.GATE_SCAN_DET_SIZE, "ai_threads": insightface_utils.ai_threads(),
                "processors": os.cpu_count(), "memory_mb": round(memory_end) if memory_end else None,
            }))
            return
        self.stdout.write(
            f"Frame {frame.shape[1]}x{frame.shape[0]}, detection size {settings.GATE_SCAN_DET_SIZE}, "
            f"{options['cores'] or 'all'} cores, {faces} face(s) found\n"
            f"Model loading: {load_seconds:.1f} s\n"
            f"Scan speed:    {average:.0f} ms per frame on average ({1000 / average:.1f} frames/s), "
            f"95% of frames under {slowest:.0f} ms\n"
            + (f"Memory:        {memory_end:.0f} MB in use (face models took about "
               f"{memory_end - memory_start:.0f} MB)" if memory_end else "Memory:        not available on this OS")
        )
