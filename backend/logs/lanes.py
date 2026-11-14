"""A gate's two cameras - the entrance and the exit - as two "lanes".

Every gate has an entrance camera, and can have an exit camera beside it
(chosen in that gate's monitor). Each camera's working state is kept apart
under its own lane name, so the two never mix: the frame votes (Recognition/
Unmatched/Spoof/OcclusionAttempt), the face tracks (logs/motion.py), the
card-tap wait (PendingTiebreak), and the Cameras page's picture and reports
(logs/camera_monitor.py, logs/camera_status.py).

The entrance lane is the gate's own name, exactly as before exits came back,
so nothing already saved changes; the exit lane is the gate's name plus
EXIT_SUFFIX. Entry records (EntryLog) never store a lane: they keep the real
gate name, with `direction` saying which way. The gate monitor
(entry-agent/lanes.py) names its exit camera the same way.
"""

ENTRY = "entry"
EXIT = "exit"
EXIT_SUFFIX = " · Exit"


def lane(gate_location, direction):
    """The lane name for this gate's camera facing `direction`."""
    gate = gate_of(gate_location)
    return f"{gate}{EXIT_SUFFIX}" if direction == EXIT else gate


def gate_of(lane_name):
    """The real gate name of a lane (a plain gate name is its own entrance lane)."""
    return (lane_name or "").removesuffix(EXIT_SUFFIX)


def direction_of(lane_name):
    return EXIT if (lane_name or "").endswith(EXIT_SUFFIX) else ENTRY
