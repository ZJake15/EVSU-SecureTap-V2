# Speed and accuracy test mode

How the team runs the gate's speed/accuracy test (120 trials), what is
measured, and how it is counted — with a method paragraph for the paper.

## What it is

A switch on the gate monitor that **only adds measurement**. Every camera
frame goes through exactly the same face detection, quality checks, spoof
check, covered-face check, matching and 2-of-4 voting, with the same Settings
values, as on a normal day. Nothing about recognition, liveness, occlusion,
voting or the card reader is changed.

What test mode adds:

- a red **TEST MODE** label on the gate monitor and a separate test window
  showing the current trial in large text;
- a timer and a results file, written after every trial;
- a mark on every entry the test creates, so the dashboard's Live Monitoring,
  Logs, Reports and the gate's own counters leave them out;
- a "Delete all test data" button.

## Before the session

1. **Enrolled subject** — registered in SecureTap with face photos. Note
   their ID number.
2. **Unknown subject** — a volunteer who is **not** registered. Ask their
   permission first; their face data is deleted at the end (see Clean-up).
3. **Spoof** — a printed photo (or phone/tablet screen) of the enrolled
   subject.
4. **Floor marks** at 2, 3, 4 and 5 m from the camera, measured with a tape.
5. Open **EVSU SecureTap Admin** → Entry Agent settings → tick
   **"Open the gate monitor in TEST MODE"** → open the Entry Agent. (The box
   is never saved; it's off again next time.)

## Running it

In the test window, fill in once per session: the enrolled subject's ID
number, a label for each subject (e.g. "Subject A", "Volunteer 1" — these go
in the results, not real names), the spoof medium, and the timeout (10 s).
**Begin session** checks the ID and saves every setting to a file.

Each trial:

1. The window shows the trial — e.g. **Trial 7 of 120 · SPOOF · 5 m**.
2. The subject stands on that mark, **facing away** from the camera.
3. Wait for **READY** (it appears once no face has been seen for 4 s, so the
   previous trial can't carry over).
4. Press **Start (F8)**; the subject turns to face the camera and stands
   still.
5. The trial ends by itself when the gate decides, or after the timeout. The
   result shows in the window and is already saved. The next trial comes up.

- **Redo (F9)** — something went wrong (someone walked in front, the subject
  wasn't ready). During a trial, it's abandoned; just after one, that result
  is marked "redone" (kept in the file, but not counted) and the trial comes
  back.
- **Skip (F10)** — a trial can't be done; type a short reason. Skipped trials
  are not counted.
- Stopping and carrying on later is fine: the window always continues at the
  first trial without a result.

## The trial plan

`trial_plan.csv`: 3 categories (enrolled, spoof, unknown) × 4 distances
(2, 3, 4, 5 m) × 10 = **120 trials**, in 10 rounds. Each round has all 12
combinations once, in a shuffled order (fixed seed 2026, so the order can be
reproduced). Balanced rounds mean tiredness or a change in lighting during
the session affects every combination about equally.

## What is measured

All times use the computer's high-resolution clock (`time.perf_counter`).
Each time is measured when the gate's answer reaches the gate monitor, just
before it is drawn. The screen redraws every ~66 ms, so what the guard sees
can lag the recorded time by up to about 0.1 s.

| Time | From | To |
|---|---|---|
| **Decision time** (main measure) | the camera frame in which the face was first usable (passed every quality check) | the gate's final decision |
| Decision time from first face | the first frame with any face (even turning) | the final decision |
| Time to first detection | pressing Start | the first answer with a face in it |

The main measure starts at the first *usable* frame so it does not depend on
how quickly the subject turns around. Frames of a turning, moving or blurred
face are skipped by the quality checks (counted in the results).

**Final decision** = the voting agreed (the gate wrote a record): Match,
Spoof or Unknown. A card-tap request (two people look too alike) also ends
the trial. If nothing is decided within the timeout:

- **Not detected** — no face was seen at all;
- **Occluded** — the gate kept asking to uncover the face;
- **No decision** — a face was seen, but nothing was decided.

**Correct** — enrolled: a Match *to the right person*; spoof: Spoof; unknown:
Unknown. Anything else, including Not detected, is not correct.

**Repeat entry** — the gate doesn't write a second record for the same
person within its repeat-entry cooldown (60 s for a match). Such a trial is
still decided by the same voting, and its time is just as valid; the
`repeat_entry` column marks it.

## The files

In `entry-agent\test-results\` (an installed copy: its data folder; the
**Open results folder** button opens it):

- `results.csv` — one row per trial (see the columns below);
- `trial_plan.csv` — the plan;
- `session_<date-time>_settings.json` — every Settings value, the model
  setup, the camera and gate monitor settings for that session (for the
  paper's "materials" section).

Columns of `results.csv`: trial, round, session_id, date_time, category,
distance_m, subject_label, spoof_medium, time_to_first_detection_ms,
decision_time_ms, decision_time_from_first_face_ms, frames_processed,
frames_with_face, frames_skipped_turned / _moving / _blur / _edge,
frames_covered, frames_face_beyond_limit, result, expected_result, correct,
repeat_entry, matched_name, matched_id, match_confidence, best_similarity,
liveness_score, face_width_px, face_height_px, estimated_distance_m,
frame_size_sent, camera_resolution, note, status (final / redone).

## The summary

```
.venv\Scripts\python.exe entry-agent\test_mode_summary.py
```

(On the development computer. It needs `matplotlib`, which the app itself
doesn't ship: `.venv\Scripts\python.exe -m pip install matplotlib` once.
Give it the path of a results.csv copied from another computer if needed.)

Writes next to `results.csv`:

- `summary_table.csv` — per category × distance (and per category overall):
  trials, correct, success rate with a 95% confidence interval (Wilson),
  mean / median / standard deviation of the decision time, mean confidence,
  mean liveness score;
- `result_breakdown.csv` — how many trials ended in each result;
- `decision_time_vs_distance.png`, `success_rate_vs_distance.png`.

Success rate counts every trial, so Not detected / No decision / Occluded
lower it. Decision-time figures use only the trials that reached a decision.

## Clean-up

**Delete all test data…** in the test window (or
`python manage.py delete_test_data` in `backend`) deletes every test entry and
its photo, and the face data the scan kept during the test sessions —
including the unknown volunteer's. Real entries are not touched. The results
files are kept. It is recorded in the Audit Log.

## For the paper (method)

> Speed and accuracy were measured with the system's test mode, which
> records timing without altering the recognition pipeline or its settings
> (a snapshot of all settings was saved for each session). Three subject
> categories were tested — an enrolled user, a presentation attack (a
> [printed photo] of the enrolled user) and an unregistered person — at 2, 3,
> 4 and 5 m from the camera, 10 trials per combination (120 trials), in 10
> balanced, randomized rounds. In each trial the subject faced away from the
> camera, the trial was started, and the subject turned to face the camera.
> Decision time was measured with a high-resolution monotonic clock from the
> capture of the first frame in which the face passed the system's quality
> checks to the system's final decision (agreement of 2 of the last 4 frames);
> a trial with no decision within 10 s was recorded as not detected (no face
> seen) or no decision. A trial was correct if the enrolled user was matched
> to their own record, the attack was rejected as a spoof, or the
> unregistered person was rejected as unknown. Success rates are reported
> with 95% Wilson confidence intervals.
