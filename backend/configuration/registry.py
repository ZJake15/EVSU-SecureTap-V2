"""Every setting on the dashboard's Settings page, defined in one place.

Each entry says what kind of value the setting holds, its allowed range
(checked on the server - see store.validate), the recommended range for the
risky ones, the plain-English label and description the page shows, and its
default. A default that already existed before the Settings page reads the
current value from settings.py (which reads backend/.env), so the first
time the system starts with this page it behaves exactly as it did before.
New features default to OFF.
"""

from dataclasses import dataclass

from django.conf import settings

INT, FLOAT, BOOL, CHOICE, FIXED = "int", "float", "bool", "choice", "fixed"


def _from_settings(name):
    """Default = whatever settings.py/.env currently says (day-one value)."""
    return lambda: getattr(settings, name)


@dataclass(frozen=True)
class SettingDef:
    key: str
    section: str
    label: str
    description: str
    kind: str
    default: object
    minimum: float = None
    maximum: float = None
    step: float = None
    unit: str = ""
    # Numbers: (low, high). On/off switches: the safe value (True/False).
    recommended: object = None
    # The value "Reset to recommended" puts back.
    recommended_value: object = None
    # Changing it outside the recommended range needs a confirmation step.
    risky: bool = False
    choices: tuple = ()
    note: str = ""
    # Shown when this on/off switch is turned off.
    off_warning: str = ""
    # Key of an on/off switch that must be on for this setting to matter.
    depends_on: str = ""
    requires_restart: bool = False

    def default_value(self):
        return self.default() if callable(self.default) else self.default


SECTIONS = [
    ("face", "Face Recognition", "How the camera decides who someone is."),
    ("spoof", "Anti-Spoofing (Fake Face Check)", "Stops someone using a printed photo or a phone screen of another student."),
    ("quality", "Photo Quality Checks", "Which camera frames are good enough to use."),
    ("nfc", "NFC Card", "When the system asks for a card tap instead of deciding from the face alone."),
    ("enrollment", "Enrollment", "Registering a new student or staff member."),
    ("privacy", "Privacy & Data Retention", "How long records and photos are kept before they are deleted."),
    ("security", "Security & Accounts", "Signing in to the dashboard and at the gate monitor."),
    ("alerts", "Alerts", "Warnings shown to the guard on the gate monitor."),
    ("advanced", "Advanced", "Fine-tuning that most schools never need to change."),
]

DEFINITIONS = [
    # ---- 1. Face Recognition -------------------------------------------------
    SettingDef(
        "match_strictness", "face", "Match strictness",
        "How similar a face must be to a saved photo before the system accepts it as a match. "
        "Higher means fewer wrong people get in, but more real students get rejected.",
        FLOAT, _from_settings("FACE_MATCH_SIMILARITY_THRESHOLD"), 0.20, 0.90, 0.01,
        recommended=(0.40, 0.55), recommended_value=0.45, risky=True,
    ),
    SettingDef(
        "frames_must_agree", "face", "Frames that must agree",
        "How many camera frames must reach the same answer before the system decides. "
        "Higher is safer but slightly slower.",
        INT, _from_settings("VOTE_REQUIRED_AGREEMENT"), 1, 10, 1, unit="frames",
    ),
    SettingDef(
        "frames_considered", "face", "Out of the last",
        "How many of the most recent camera frames are counted when the system decides. "
        "Must be at least the number of frames that must agree.",
        INT, _from_settings("VOTE_WINDOW_SIZE"), 1, 20, 1, unit="frames",
    ),
    SettingDef(
        "decision_window_seconds", "face", "Scan window length",
        "Only camera frames from the last this-many seconds count toward a decision. "
        "Older frames are ignored, so someone who stepped away starts fresh.",
        INT, _from_settings("VOTE_WINDOW_SECONDS"), 1, 30, 1, unit="seconds",
    ),
    # ---- 2. Anti-Spoofing ---------------------------------------------------
    SettingDef(
        "spoof_check_enabled", "spoof", "Spoof checking",
        "Checks that the face in front of the camera is a real person, not a printed photo or a phone screen.",
        BOOL, True, recommended=True, recommended_value=True, risky=True,
        off_warning="Turning this off removes the protection against someone using a photo or phone "
                    "screen of another student.",
    ),
    SettingDef(
        "spoof_strictness", "spoof", "Spoof check strictness",
        "How sure the system must be that it's looking at a real person and not a photo or phone screen. "
        "Higher catches more fakes, but may stop more real students.",
        FLOAT, _from_settings("LIVENESS_SCORE_THRESHOLD"), 0.10, 0.95, 0.01,
        recommended=(0.40, 0.60), recommended_value=0.50, risky=True, depends_on="spoof_check_enabled",
    ),
    SettingDef(
        "spoof_unsure_range", "spoof", "Ask-for-card range",
        "When the fake-face check is close to its limit but not clearly fake, ask for a card tap instead of "
        "treating the person as a fake. This sets how close counts as unsure. 0 turns this off.",
        FLOAT, 0.0, 0.0, 0.30, 0.01,
        note="Off (0) by default. 0.10 is a good starting point if you turn it on.",
        depends_on="spoof_check_enabled",
    ),
    # ---- 3. Photo Quality Checks ----------------------------------------------
    SettingDef(
        "blur_min", "quality", "Blur sensitivity",
        "How sharp a camera frame must be to be used. Higher skips more blurry frames and waits for a "
        "clearer one; lower accepts blurrier frames.",
        FLOAT, _from_settings("GATE_SCAN_MIN_BLUR_VARIANCE"), 0, 200, 1,
    ),
    SettingDef(
        "whole_face_required", "quality", "Require the whole face in view",
        "If part of the face is cut off at the edge of the camera, skip that frame instead of guessing.",
        BOOL, True,
    ),
    SettingDef(
        "covered_face_enabled", "quality", "Covered face detection",
        "Asks people to uncover their face when a hand, mask or scarf is covering it, instead of trying "
        "to recognize a covered face.",
        BOOL, True,
    ),
    SettingDef(
        "covered_face_cutoff", "quality", "Covered face strictness",
        "How sure the system must be that a face is covered before asking the person to uncover it. "
        "Lower catches more coverings, but asks more people by mistake.",
        FLOAT, _from_settings("OCCLUSION_CLASSIFIER_THRESHOLD"), 0.05, 0.95, 0.01,
        note='Used with the "Trained model" detection method (see Advanced).',
        depends_on="covered_face_enabled",
    ),
    # ---- 4. NFC Card ----------------------------------------------------------
    SettingDef(
        "card_wait_seconds", "nfc", "Card tap time limit",
        "How many seconds the system waits for a card tap when it asks for one. After that, the attempt "
        "is recorded as not confirmed.",
        INT, _from_settings("TIEBREAK_TIMEOUT_SECONDS"), 3, 60, 1, unit="seconds",
    ),
    SettingDef(
        "ask_card_when_unsure", "nfc", "Ask for card when unsure",
        "When the system can't decide between two students, or a match is only just good enough, ask for "
        "a card tap instead of guessing.",
        BOOL, True,
    ),
    SettingDef(
        "unsure_margin", "nfc", "How close counts as unsure",
        "How close two students' results (or a result and the match limit) must be before the system asks "
        "for a card. Higher asks for cards more often.",
        FLOAT, _from_settings("TIEBREAK_MARGIN"), 0.0, 0.20, 0.01, depends_on="ask_card_when_unsure",
    ),
    SettingDef(
        "lookalikes_always_tap", "nfc", "Always ask look-alikes for a card",
        "For pairs flagged as look-alikes or twins, always require a card tap, even when the system seems "
        "confident.",
        BOOL, True,
    ),
    # ---- 5. Enrollment --------------------------------------------------------
    SettingDef(
        "photos_required", "enrollment", "Number of photos required",
        "How many photos each person gives with the guided capture: front, slight left, slight right, "
        "no expression and smile.",
        FIXED, 5, unit="photos",
        note="Fixed at 5 - each photo is a specific pose with its own guide drawing.",
    ),
    SettingDef(
        "allow_single_photo", "enrollment", "Allow single-photo registration",
        "Lets staff register someone with just one photo. Faster, but matching is less reliable for "
        "that person.",
        BOOL, True,
        note="Applies to the Add Person single-photo option. Bulk import is not affected.",
    ),
    SettingDef(
        "lookalike_sensitivity", "enrollment", "Look-alike detection sensitivity",
        "How similar two people's faces must be before the system warns staff they might get mixed up. "
        "Lower warns about more pairs.",
        FLOAT, _from_settings("CONFUSABLE_SIMILARITY_THRESHOLD"), 0.30, 0.95, 0.01,
    ),
    # ---- 7. Privacy & Data Retention -----------------------------------------
    SettingDef(
        "auto_delete_enabled", "privacy", "Automatic deletion",
        "Deletes old entry records, gate photos and audit log entries once a day, using the periods below. "
        "Registered students' own photos and face data are never deleted.",
        BOOL, False,
        note="Off by default, so no records are deleted until you turn it on. Unknown face data (below) and "
             "photo files left without a record are always deleted.",
    ),
    SettingDef(
        "keep_entry_records_days", "privacy", "How long to keep entry records",
        "Entry and exit records older than this are deleted.",
        INT, 365, 7, 3650, 1, unit="days", depends_on="auto_delete_enabled",
    ),
    SettingDef(
        "keep_unknown_face_days", "privacy", "How long unknown face data is kept",
        "Face data from people who aren't registered is deleted after 1 day, even with Automatic deletion "
        "off - nothing needs it longer.",
        FIXED, 1, unit="day",
        note="These are people who never agreed to have their face stored. Their gate photo follows the "
             "gate photo period below.",
    ),
    SettingDef(
        "keep_gate_photos_days", "privacy", "How long to keep gate photos",
        "Photos captured at the gate (of unknown faces and suspected fakes) older than this are deleted. "
        "The entry record itself stays.",
        INT, 90, 1, 3650, 1, unit="days", depends_on="auto_delete_enabled",
    ),
    # ---- 8. Security & Accounts ----------------------------------------------
    SettingDef(
        "idle_logout_enabled", "security", "Automatic logout",
        "Logs a dashboard user out after a period with no activity.",
        BOOL, False,
    ),
    SettingDef(
        "idle_logout_minutes", "security", "Log out after",
        "How long a dashboard user can be inactive before being logged out.",
        INT, 15, 1, 720, 1, unit="minutes", depends_on="idle_logout_enabled",
    ),
    SettingDef(
        "lockout_enabled", "security", "Failed login lockout",
        "Temporarily locks an account after too many wrong passwords in a row.",
        BOOL, False,
    ),
    SettingDef(
        "lockout_attempts", "security", "Wrong passwords before locking",
        "How many wrong passwords in a row lock the account.",
        INT, 5, 3, 20, 1, unit="attempts", depends_on="lockout_enabled",
    ),
    SettingDef(
        "lockout_minutes", "security", "Lock for",
        "How long a locked account has to wait before it can try again.",
        INT, 15, 1, 1440, 1, unit="minutes", depends_on="lockout_enabled",
    ),
    SettingDef(
        "gate_sign_in_enabled", "security", "Guards sign in at the gate monitor",
        "The guard on duty signs in at the gate monitor - with their password, or by tapping their own staff "
        "ID card (set on the Accounts page) - so every entry shows who was on duty. The gate keeps scanning "
        "when nobody is signed in; those entries are marked Unattended.",
        BOOL, False,
        note="A Security Officer can sign in only at their assigned gate; an Admin or SASO at any gate.",
    ),
    SettingDef(
        "gate_shift_hours", "security", "End a shift after",
        "Signs the guard out by itself after this long, in case they forget - the next guard then signs in.",
        INT, 12, 1, 24, 1, unit="hours", depends_on="gate_sign_in_enabled",
    ),
    SettingDef(
        "keep_audit_log_days", "security", "How long to keep the audit log",
        "The audit log is the record of who changed what. Entries older than this are deleted. "
        "0 keeps it forever.",
        INT, 0, 0, 36500, 1, unit="days",
        note="Only applies when Automatic deletion (Privacy) is on.",
    ),
    # ---- 9. Alerts -------------------------------------------------------------
    SettingDef(
        "alert_spoof", "alerts", "Alert on suspected fake face",
        "Shows a warning and sounds the alarm on the gate monitor when someone appears to be using a "
        "photo or screen.",
        BOOL, True,
    ),
    SettingDef(
        "alert_repeated_unknown", "alerts", "Alert on repeated unknown faces",
        "Warns the guard when the same unrecognized person keeps appearing at a gate.",
        BOOL, False,
    ),
    SettingDef(
        "repeated_unknown_count", "alerts", "Times seen",
        "How many times the same unrecognized face must appear before the guard is warned.",
        INT, 3, 2, 20, 1, unit="times", depends_on="alert_repeated_unknown",
    ),
    SettingDef(
        "repeated_unknown_minutes", "alerts", "Within",
        "The time span those sightings must fall within.",
        INT, 10, 1, 240, 1, unit="minutes", depends_on="alert_repeated_unknown",
    ),
    # ---- Advanced (existing settings not in the brief) -------------------------
    SettingDef(
        "max_turn", "advanced", "How far a face can be turned",
        "How far someone can turn their head away from the camera before the frame is skipped. "
        "Higher accepts more turned faces.",
        FLOAT, _from_settings("FACE_MAX_YAW_RATIO"), 0.10, 1.00, 0.01,
    ),
    SettingDef(
        "edge_margin", "advanced", "Edge margin",
        "How close to the edge of the picture a face can be before it counts as cut off.",
        FLOAT, _from_settings("FACE_EDGE_MARGIN_RATIO"), 0.0, 0.20, 0.01, depends_on="whole_face_required",
    ),
    SettingDef(
        "covered_face_mode", "advanced", "Covered face detection method",
        "How the system decides a face is covered: a model trained on example photos, or three simple "
        "rules. If the trained model's file is missing, simple rules are used automatically.",
        CHOICE, _from_settings("OCCLUSION_DETECTION_MODE"),
        choices=(("classifier", "Trained model"), ("rules", "Simple rules")),
        depends_on="covered_face_enabled",
    ),
    SettingDef(
        "covered_rule_mouth", "advanced", "Simple rules: mouth visibility",
        "With simple rules, a face counts as covered if the mouth looks narrower than this.",
        FLOAT, _from_settings("FACE_MIN_MOUTH_VISIBILITY_RATIO"), 0.30, 1.20, 0.01,
        depends_on="covered_face_enabled",
    ),
    SettingDef(
        "covered_rule_texture", "advanced", "Simple rules: lower-face detail",
        "With simple rules, a face counts as covered if the lower face looks smoother than this.",
        FLOAT, _from_settings("FACE_MAX_MOUTH_TEXTURE_RATIO"), 0.05, 1.50, 0.01,
        depends_on="covered_face_enabled",
    ),
    SettingDef(
        "covered_rule_det_score", "advanced", "Simple rules: face clarity",
        "With simple rules, a face counts as covered if the camera is less sure than this that it's "
        "seeing a whole face.",
        FLOAT, _from_settings("FACE_MIN_DET_SCORE_UNOCCLUDED"), 0.10, 0.99, 0.01,
        depends_on="covered_face_enabled",
    ),
    SettingDef(
        "recognition_cooldown_seconds", "advanced", "Repeat entry cooldown",
        "A recognized person seen again within this many seconds isn't recorded twice.",
        INT, _from_settings("RECOGNITION_COOLDOWN_SECONDS"), 5, 3600, 1, unit="seconds",
    ),
    SettingDef(
        "unknown_cooldown_seconds", "advanced", "Repeat unknown-face cooldown",
        "The same unrecognized face seen again within this many seconds isn't recorded twice.",
        INT, _from_settings("UNENROLLED_CAPTURE_COOLDOWN_SECONDS"), 5, 3600, 1, unit="seconds",
    ),
    SettingDef(
        "spoof_cooldown_seconds", "advanced", "Repeat fake-face cooldown",
        "The same suspected fake seen again within this many seconds isn't recorded twice.",
        INT, _from_settings("SPOOF_CAPTURE_COOLDOWN_SECONDS"), 5, 3600, 1, unit="seconds",
    ),
]

BY_KEY = {definition.key: definition for definition in DEFINITIONS}
