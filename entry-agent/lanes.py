"""A gate's two cameras - the entrance and the exit (see backend logs/lanes.py).

The gate monitor sends every face check and card tap with the real gate name
and a direction; the backend keeps each camera's working state apart itself.
Only the two per-camera reports - its live picture for the Cameras page and
what it runs at for Settings > Camera - are sent under the camera's own lane
name, so the dashboard shows the exit camera as its own tile and row.
"""

ENTRY = "entry"
EXIT = "exit"
DIRECTIONS = (ENTRY, EXIT)
EXIT_SUFFIX = " · Exit"  # keep in step with backend/logs/lanes.py
LABELS = {ENTRY: "ENTRANCE", EXIT: "EXIT"}


def lane(gate_location, direction):
    return f"{gate_location}{EXIT_SUFFIX}" if direction == EXIT else gate_location
