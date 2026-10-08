"""How fast can this computer run the gate's face scan?

    python manage.py benchmark_scan                 # 30 frames, today's settings
    python manage.py benchmark_scan --det-size 384  # try a smaller detection size
    python manage.py benchmark_scan --cores 4       # pretend this PC only has 4 cores

Runs the same heavy steps the gate does for every camera frame - find the
faces, make each face's fingerprint, run the fake-face check - on one
enrolled photo, many times, and reports the time per frame and the memory
used. Nothing is written to the database. Run it on the presentation laptop
itself to see its real numbers.
"""

import ctypes
import statistics
import time
from ctypes import wintypes

import cv2
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from users import insightface_utils, liveness_utils
from users.models import FaceEmbedding


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

    def handle(self, *args, **options):
        if options["cores"]:
            _limit_cores(options["cores"])
        if options["det_size"]:
            settings.GATE_SCAN_DET_SIZE = options["det_size"]

        sample = FaceEmbedding.objects.exclude(source_image="").exclude(source_image__isnull=True).first()
        if sample is None:
            raise CommandError("No enrolled photo to test with - register someone first.")
        with sample.source_image.open("rb") as fh:
            data = np.frombuffer(fh.read(), dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        height, width = frame.shape[:2]
        if width > options["width"]:
            scale = options["width"] / width
            frame = cv2.resize(frame, (options["width"], round(height * scale)), interpolation=cv2.INTER_AREA)

        memory_start = _memory_mb()
        load_start = time.perf_counter()
        insightface_utils.compute_face_embeddings_and_boxes(frame)  # loads the gate's face models
        with sample.source_image.open("rb") as fh:
            try:
                insightface_utils.compute_face_embedding(fh)  # loads what enrollment uses
            except ValueError:
                pass  # a photo failing enrollment's quality bar doesn't matter here
        load_seconds = time.perf_counter() - load_start

        timings, faces = [], 0
        for _ in range(options["frames"]):
            start = time.perf_counter()
            detections = insightface_utils.compute_face_embeddings_and_boxes(frame)
            for detection in detections:
                liveness_utils.compute_liveness_score(frame, detection[1])
            timings.append((time.perf_counter() - start) * 1000)
            faces = len(detections)
        memory_end = _memory_mb()

        average = statistics.mean(timings)
        slowest = sorted(timings)[int(len(timings) * 0.95) - 1] if len(timings) > 1 else timings[0]
        self.stdout.write(
            f"Frame {frame.shape[1]}x{frame.shape[0]}, detection size {settings.GATE_SCAN_DET_SIZE}, "
            f"{options['cores'] or 'all'} cores, {faces} face(s) found\n"
            f"Model loading: {load_seconds:.1f} s\n"
            f"Scan speed:    {average:.0f} ms per frame on average ({1000 / average:.1f} frames/s), "
            f"95% of frames under {slowest:.0f} ms\n"
            + (f"Memory:        {memory_end:.0f} MB in use (face models took about "
               f"{memory_end - memory_start:.0f} MB)" if memory_end else "Memory:        not available on this OS")
        )
