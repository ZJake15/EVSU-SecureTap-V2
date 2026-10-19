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
# A time of day, "HH:MM" (24-hour), and a set of weekdays, "mon,tue,..." -
# both kept as text, in WEEKDAYS order (see store.validate).
TIME, DAYS = "time", "days"
WEEKDAYS = (("mon", "Mon"), ("tue", "Tue"), ("wed", "Wed"), ("thu", "Thu"), ("fri", "Fri"), ("sat", "Sat"),
            ("sun", "Sun"))


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
    ("camera", "Camera",
     "How every gate's camera takes its picture and how often faces are checked. Auto keeps what each "
     "computer's speed mode (in the launcher) picks."),
    ("nfc", "NFC Card", "When the system asks for a card tap instead of deciding from the face alone."),
    ("enrollment", "Enrollment", "Registering a new student or staff member."),
    ("privacy", "Privacy & Data Retention",
     "What's always deleted. How long other records are kept, and when they're deleted, is under Scheduling."),
    ("scheduling", "Scheduling",
     "What happens on a timetable: deleting old records, asking for new face photos, and the gate's opening hours."),
    ("security", "Security & Accounts", "Signing in to the dashboard and at the gate monitor."),
    ("alerts", "Alerts", "Warnings shown to the guard on the gate monitor."),
]

# Fine-tuning most schools never need to change - shown at the bottom of
# their own group, under a collapsed "More options" (there's no separate
# Advanced group any more).
MORE_OPTIONS = {
    "recognition_cooldown_seconds", "unknown_cooldown_seconds",  # Face Recognition
    "spoof_cooldown_seconds",  # Anti-Spoofing
    "max_turn", "edge_margin", "distance_correction", "show_face_distance",  # Photo Quality Checks
    "covered_face_mode", "covered_rule_mouth", "covered_rule_texture", "covered_rule_det_score",
    "student_display_fps", "camera_exposure", "backlight_compensation", "camera_focus",  # Camera
}

# The Camera group - sent to every gate monitor with each scan answer
# (logs.views.IdentifyView._agent_info), which applies a change within a
# second. "auto" = what that computer's speed mode picks (device_setup.py),
# exactly as before this group existed.
CAMERA_KEYS = (
    "camera_resolution", "camera_fps", "scan_pause", "upload_size", "video_fps", "student_display_fps",
    "camera_exposure", "backlight_compensation", "camera_focus",
)

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
        "How sharp a face must look to be used - measured the same way however near or far the face is. "
        "Higher skips more smeared or blurry frames and waits for a clearer one; lower accepts blurrier "
        "frames.",
        FLOAT, 10.0, 0, 300, 1,
        note="Close-up photos score 190 or more, but a still face 2-5 m from the gate camera can score as low "
             "as about 20 (capstone pilot) - 60 skipped nearly every frame there. 10 keeps still faces and skips "
             "only badly smeared frames; fast movement itself is caught by \"Fast movement limit\".",
    ),
    SettingDef(
        "max_face_speed", "quality", "Fast movement limit",
        "A face moving faster than this between camera frames is smeared in the photo, so that frame isn't "
        "used - the box shows Checking... until the person slows down, instead of a wrong Unknown. Measured in "
        "face widths per second, so it means the same near and far. 0 turns this off.",
        FLOAT, 4.0, 0, 20, 0.5, unit="widths/s",
        note="At a typical webcam's shutter speed, a face smears by about a tenth of its width at 3 widths/s "
             "(still matches) and a fifth at 6 (stops matching). Walking toward the camera barely counts.",
    ),
    SettingDef(
        "whole_face_required", "quality", "Require the whole face in view",
        "If part of the face is cut off at the edge of the camera, skip that frame instead of guessing.",
        BOOL, True,
    ),
    SettingDef(
        "max_scan_distance", "quality", "Ignore faces farther than",
        "Faces farther from the camera than this are ignored - never checked, matched, or recorded as unknown "
        "or as a fake. A face that far away is too small to check reliably. 0 checks every face it can see.",
        FLOAT, 5.0, 0.0, 15.0, 0.5, unit="metres",
        note="Estimated from the size of each face in the picture and the gate's camera zoom. Check it once "
             "with Show distance on face boxes (More options, below), and if it's off, set Distance correction there.",
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
        note='Used with the "Trained model" detection method (see More options, below).',
        depends_on="covered_face_enabled",
    ),
    # ---- 3b. Camera ------------------------------------------------------------
    # Under each one, the Settings page shows what every gate's camera actually
    # does (logs/camera_status.py - the gate monitors report it).
    SettingDef(
        "camera_resolution", "camera", "Camera resolution",
        "How detailed a picture the camera takes. Higher keeps faces far away sharper but works the computer "
        "harder. It only helps together with camera zoom (launcher) above 1x or more \"Detail sent for face "
        "checks\" - otherwise the picture is shrunk again before checking.",
        CHOICE, "auto",
        choices=(("auto", "Auto (720p)"), ("640x480", "480p"), ("1280x720", "720p"), ("1920x1080", "1080p"),
                 ("2560x1440", "1440p")),
        note="A camera without the chosen size uses its nearest. After changing it, check the distance shown on "
             "the face boxes at a measured spot - a differently shaped picture can change it.",
    ),
    SettingDef(
        "camera_fps", "camera", "Camera frame rate",
        "How many pictures a second the camera is asked for. A higher rate means less motion blur in each "
        "picture. Not how often faces are checked - that's the pause below.",
        CHOICE, "auto", choices=(("auto", "Auto"), ("15", "15"), ("24", "24"), ("30", "30"), ("60", "60")),
        unit="a second",
        note="A camera that can't do the chosen rate gives its nearest; under each gate is the rate it measures. "
             "In dim light many cameras slow down to lengthen each shot (the EMEET: about 17 a second indoors) - "
             "a faster Exposure (More options) gets the full rate back, at the cost of a darker picture.",
    ),
    SettingDef(
        "scan_pause", "camera", "Pause between face checks",
        "After each face check the gate waits this long before checking the next picture. Shorter decides "
        "faster - a decision needs 2 agreeing checks - but keeps the computer busier.",
        CHOICE, "auto",
        choices=(("auto", "Auto"), ("0.05", "0.05 s"), ("0.1", "0.1 s"), ("0.2", "0.2 s"), ("0.35", "0.35 s"),
                 ("0.5", "0.5 s")),
        note="Auto = the computer's speed mode: 0.1 s Fast, 0.2 s Standard, 0.35 s Light.",
    ),
    SettingDef(
        "upload_size", "camera", "Detail sent for face checks",
        "How wide each picture is when its faces are checked. Wider keeps more detail in faces far away - better "
        "matching and fake-face checks at 4-5 m - but each check takes longer.",
        CHOICE, "auto", choices=(("auto", "Auto"), ("640", "640 px"), ("960", "960 px"), ("1280", "1280 px")),
        note="Auto = 960 px (640 px in the Light speed mode). A picture already smaller is sent as it is.",
    ),
    SettingDef(
        "video_fps", "camera", "Gate monitor video smoothness",
        "How many times a second the guard's screen redraws the camera picture. Only how smooth it looks - "
        "not what's checked.",
        CHOICE, "auto", choices=(("auto", "Auto"), ("10", "10"), ("15", "15"), ("20", "20"), ("30", "30")),
        unit="a second",
        note="Auto = 20 Fast, 15 Standard, 12 Light. Lower it if the gate monitor feels sluggish.",
    ),
    SettingDef(
        "student_display_fps", "camera", "Student display smoothness",
        "How many times a second the student-facing screen redraws the camera picture.",
        CHOICE, "auto", choices=(("auto", "Auto"), ("5", "5"), ("10", "10"), ("15", "15"), ("30", "30")),
        unit="a second",
        note="Auto = 15 Fast, 10 Standard, 6 Light.",
    ),
    SettingDef(
        "camera_exposure", "camera", "Exposure (shutter speed)",
        "A faster shutter freezes people walking - less motion blur - but makes the picture darker. Use it only "
        "where the gate is well lit.",
        CHOICE, "auto", choices=(("auto", "Auto"), ("-6", "1/64 s"), ("-7", "1/128 s"), ("-8", "1/256 s")),
        note="Auto = the camera adjusts its own exposure. Depends on the camera - under each gate, whether its "
             "camera took it.",
    ),
    SettingDef(
        "backlight_compensation", "camera", "Backlight compensation",
        "For a gate with bright daylight behind the people walking in, which makes their faces come out dark.",
        CHOICE, "auto", choices=(("auto", "Camera's own"), ("on", "On"), ("off", "Off")),
        note="Depends on the camera - under each gate, whether its camera took it.",
    ),
    SettingDef(
        "camera_focus", "camera", "Focus",
        "Autofocus can \"hunt\" and blur faces 3-5 m away as people move. Locked turns it off and keeps the "
        "focus where it is when the camera starts.",
        CHOICE, "auto", choices=(("auto", "Autofocus"), ("locked", "Locked")),
        note="Depends on the camera - under each gate, whether its camera took it. If faces look soft when "
             "locked, go back to Autofocus.",
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
        "keep_unknown_face_days", "privacy", "How long unknown face data is kept",
        "Face data from people who aren't registered is deleted after 1 day, even with Automatic deletion "
        "off - nothing needs it longer.",
        FIXED, 1, unit="day",
        note="These are people who never agreed to have their face stored. Their gate photo follows the "
             "gate photo period under Scheduling. Photo files left without a record are always deleted too.",
    ),
    # ---- 8. Scheduling ---------------------------------------------------------
    # Automatic deletion - the periods moved here from Privacy and Security
    # (same keys, so saved values carry over); the timetable is new.
    SettingDef(
        "auto_delete_enabled", "scheduling", "Automatic deletion",
        "Deletes old entry records, gate photos and audit log entries on the schedule below, using these "
        "periods. Registered students' own photos and face data are never deleted.",
        BOOL, False,
        note="Off by default, so no records are deleted until you turn it on.",
    ),
    SettingDef(
        "keep_entry_records_days", "scheduling", "How long to keep entry records",
        "Entry records older than this are deleted.",
        INT, 365, 7, 3650, 1, unit="days", depends_on="auto_delete_enabled",
    ),
    SettingDef(
        "keep_gate_photos_days", "scheduling", "How long to keep gate photos",
        "Photos captured at the gate (of unknown faces and suspected fakes) older than this are deleted. "
        "The entry record itself stays.",
        INT, 90, 1, 3650, 1, unit="days", depends_on="auto_delete_enabled",
    ),
    SettingDef(
        "keep_audit_log_days", "scheduling", "How long to keep the audit log",
        "The audit log is the record of who changed what. Entries older than this are deleted. "
        "0 keeps it forever.",
        INT, 0, 0, 36500, 1, unit="days", depends_on="auto_delete_enabled",
    ),
    SettingDef(
        "cleanup_frequency", "scheduling", "Run the clean-up",
        "How often the old records above are deleted.",
        CHOICE, "daily", choices=(("daily", "Every day"), ("weekly", "Every week"), ("monthly", "Every month")),
        depends_on="auto_delete_enabled",
    ),
    SettingDef(
        "cleanup_weekday", "scheduling", "On (every week)",
        "The day it runs, when it runs every week.",
        CHOICE, "sun", choices=WEEKDAYS, depends_on="auto_delete_enabled",
    ),
    SettingDef(
        "cleanup_month_day", "scheduling", "On day (every month)",
        "The day of the month it runs, when it runs every month - up to 28, so it happens in every month.",
        INT, 1, 1, 28, 1, depends_on="auto_delete_enabled",
    ),
    SettingDef(
        "cleanup_time", "scheduling", "At",
        "The time of day it runs. If the computer is off or SecureTap isn't running then, it runs the next "
        "time SecureTap starts.",
        TIME, "02:00", depends_on="auto_delete_enabled",
    ),
    # Face re-enrollment - see users/reenrollment.py.
    SettingDef(
        "reenroll_months", "scheduling", "Ask for new face photos every",
        "People whose newest face photos are older than this are marked Photos due in User Management, so "
        "their photos can be retaken. It's a reminder only - they can still enter. 0 turns this off.",
        INT, 0, 0, 60, 1, unit="months",
        note="12 suits most schools: faces change with age, hairstyles and glasses.",
    ),
    SettingDef(
        "reenroll_remind_days", "scheduling", "Remind before it's due",
        "Marks them Due soon this many days before their photos are due.",
        INT, 30, 0, 180, 1, unit="days",
    ),
    SettingDef(
        "reenroll_weak_matches", "scheduling", "Suggest new photos when matches get weak",
        "Marks someone Photos due when their recent face matches keep scoring low - a sign their face has "
        "changed since the photos were taken - before they start being read as Unknown.",
        BOOL, True,
        note="Weak means the middle score of their last 10 face entries is under Match strictness + 0.10.",
    ),
    SettingDef(
        "reenroll_show_at_gate", "scheduling", "Show at the gate",
        "When someone whose photos are due is recognized, the gate monitor tells the guard \"Photos due for "
        "update\", so they can be sent to have them retaken.",
        BOOL, False,
    ),
    # Gate opening hours - see configuration/schedule.py.
    SettingDef(
        "gate_hours_enabled", "scheduling", "Gate opening hours",
        "Entries outside the gate's opening hours are tagged After hours - in Logs, Live Monitoring and the "
        "CSV export - and the gate monitor alerts the guard. Scanning never stops.",
        BOOL, False,
    ),
    SettingDef(
        "gate_open_time", "scheduling", "Opens at",
        "When the gate opens each day it's open.",
        TIME, "06:00", depends_on="gate_hours_enabled",
    ),
    SettingDef(
        "gate_close_time", "scheduling", "Closes at",
        "When it closes. A closing time earlier than the opening time means open overnight (for example "
        "18:00 to 02:00).",
        TIME, "21:00", depends_on="gate_hours_enabled",
    ),
    SettingDef(
        "gate_open_days", "scheduling", "Open on",
        "The days the gate is open. On any other day, every entry is After hours.",
        DAYS, "mon,tue,wed,thu,fri,sat", depends_on="gate_hours_enabled",
    ),
    # ---- 9. Security & Accounts ----------------------------------------------
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
        BOOL, True,
        note="On by default: the gate monitor asks for a guard whenever nobody is on duty. A Security Officer "
             "can sign in only at their assigned gate; an Admin or SASO at any gate.",
    ),
    SettingDef(
        "gate_shift_hours", "security", "End a shift after",
        "Signs the guard out by itself after this long, in case they forget - the next guard then signs in.",
        INT, 12, 1, 24, 1, unit="hours", depends_on="gate_sign_in_enabled",
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
        "max_turn", "quality", "How far a face can be turned",
        "How far someone can turn their head away from the camera before the frame is skipped. "
        "Higher accepts more turned faces.",
        FLOAT, _from_settings("FACE_MAX_YAW_RATIO"), 0.10, 1.00, 0.01,
    ),
    SettingDef(
        "edge_margin", "quality", "Edge margin",
        "How close to the edge of the picture a face can be before it counts as cut off.",
        FLOAT, _from_settings("FACE_EDGE_MARGIN_RATIO"), 0.0, 0.20, 0.01, depends_on="whole_face_required",
    ),
    SettingDef(
        "distance_correction", "quality", "Distance correction",
        "Corrects the estimated distance for your camera. Turn on Show distance on face boxes, stand at a "
        "measured distance facing the camera, and set this to the real distance divided by the one shown - "
        "for example, really 5 m but shown as 1.5 m: 5 / 1.5 = 3.33.",
        FLOAT, 3.33, 0.25, 6.0, 0.01, unit="x",
        note="3.33 is measured for the EMEET SmartCam C60E 4K used at the gates (5 m showed as 1.5 m). A "
             "typical webcam needs about 1.00 - check yours once if you use a different camera.",
    ),
    SettingDef(
        "show_face_distance", "quality", "Show distance on face boxes",
        "Adds each face's estimated distance to its box on the gate monitor (for example \"UNKNOWN · 4.8 m\"), "
        "for checking the distance limit: stand at a measured spot and compare. Turn it off again afterwards.",
        BOOL, False,
    ),
    SettingDef(
        "covered_face_mode", "quality", "Covered face detection method",
        "How the system decides a face is covered: a model trained on example photos, or three simple "
        "rules. If the trained model's file is missing, simple rules are used automatically.",
        CHOICE, _from_settings("OCCLUSION_DETECTION_MODE"),
        choices=(("classifier", "Trained model"), ("rules", "Simple rules")),
        depends_on="covered_face_enabled",
    ),
    SettingDef(
        "covered_rule_mouth", "quality", "Simple rules: mouth visibility",
        "With simple rules, a face counts as covered if the mouth looks narrower than this.",
        FLOAT, _from_settings("FACE_MIN_MOUTH_VISIBILITY_RATIO"), 0.30, 1.20, 0.01,
        depends_on="covered_face_enabled",
    ),
    SettingDef(
        "covered_rule_texture", "quality", "Simple rules: lower-face detail",
        "With simple rules, a face counts as covered if the lower face looks smoother than this.",
        FLOAT, _from_settings("FACE_MAX_MOUTH_TEXTURE_RATIO"), 0.05, 1.50, 0.01,
        depends_on="covered_face_enabled",
    ),
    SettingDef(
        "covered_rule_det_score", "quality", "Simple rules: face clarity",
        "With simple rules, a face counts as covered if the camera is less sure than this that it's "
        "seeing a whole face.",
        FLOAT, _from_settings("FACE_MIN_DET_SCORE_UNOCCLUDED"), 0.10, 0.99, 0.01,
        depends_on="covered_face_enabled",
    ),
    SettingDef(
        "recognition_cooldown_seconds", "face", "Repeat entry cooldown",
        "A recognized person seen again within this many seconds isn't recorded twice.",
        INT, _from_settings("RECOGNITION_COOLDOWN_SECONDS"), 5, 3600, 1, unit="seconds",
    ),
    SettingDef(
        "unknown_cooldown_seconds", "face", "Repeat unknown-face cooldown",
        "The same unrecognized face seen again within this many seconds isn't recorded twice.",
        INT, _from_settings("UNENROLLED_CAPTURE_COOLDOWN_SECONDS"), 5, 3600, 1, unit="seconds",
    ),
    SettingDef(
        "spoof_cooldown_seconds", "spoof", "Repeat fake-face cooldown",
        "The same suspected fake seen again within this many seconds isn't recorded twice.",
        INT, _from_settings("SPOOF_CAPTURE_COOLDOWN_SECONDS"), 5, 3600, 1, unit="seconds",
    ),
]

BY_KEY = {definition.key: definition for definition in DEFINITIONS}
