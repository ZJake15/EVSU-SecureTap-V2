# Speed and accuracy test mode

How the team runs the gate's speed/accuracy test (240 trials), what is
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
3. **Spoof** — a **life-size** printed photo (or tablet screen) of the
   enrolled subject: the face as big as the real face. The system estimates
   distance from face size, so a smaller printed face looks farther away —
   in the pilot, a small print held at 4–5 m was estimated beyond 5 m and
   ignored ("No decision").
4. **Keep every setting the same for the whole test** — including the
   camera zoom (the pilot changed it from 1.0 to 2.0 between sessions).
5. **Floor marks** at 2, 3, 4 and 5 m from the camera lens, measured with a
   tape.
6. **Two people on site**: the subject on the mark, and someone at the laptop
   pressing Start — out of the camera's view (a face the camera can see keeps
   Start from becoming READY, and would get into the trials).
7. Open **EVSU SecureTap Admin** → Entry Agent settings → tick
   **"Open the gate monitor in TEST MODE"** → open the Entry Agent. (The box
   is never saved; it's off again next time.)

## Running it

In the test window, fill in once per session: the enrolled subject's ID
number, a label for each subject (e.g. "Subject A", "Volunteer 1" — these go
in the results, not real names), the spoof medium, **trials per category per
distance** (20 for the full test — fewer for a pilot), **which part** this
session runs (All, Part 1 or Part 2) and the timeout (10 s). **Begin session**
checks the ID and saves every setting to a file.

- **Pilot first**: e.g. 2 trials per condition (24 trials). Afterwards press
  **Start a new test…** — it moves the pilot's files into an `old-run-…`
  folder (nothing is deleted) — and begin the real test with 20.
- **Two days**: choose **Part 1** on the first day and **Part 2** on the
  second. Each part has exactly half of every category × distance (10 each).
- One test = one number of trials per condition. The window refuses a
  different number while the folder has results, until you start a new test.
- **Categories: "Enrolled and spoof only"** leaves the unknown volunteer's
  trials out of the plan - for one person testing alone (enrolled, holding a
  photo of their own face for the spoof trials). It is a separate test: start
  a new test to switch between it and the full three categories.

Each trial:

1. The window shows the trial — e.g. **Trial 7 of 240 · SPOOF · 5 m**.
2. The subject stands on that mark, **facing away** from the camera.
3. Wait for **READY** (it appears once no face has been seen for 4 s, so the
   previous trial can't carry over).
4. Press **Start (F8)**; the subject turns to face the camera and stands
   still.
5. The trial ends by itself when the gate decides, or after the timeout. The
   result shows in the window and is already saved. The next trial comes up.

- **Redo (F9)** — something went wrong *with the procedure* (someone walked
  in front, the subject wasn't ready or stood off the mark). Never because
  the result looks wrong — that would bias the data. During a trial, it's abandoned; just after one, that result
  is marked "redone" (kept in the file, but not counted) and the trial comes
  back.
- **Skip (F10)** — a trial can't be done; type the reason in the **Skip
  note** box, then press Skip. Skipped trials are not counted.
- Stopping and carrying on later is fine: the window always continues at the
  first trial without a result.

## The trial plan

`trial_plan.csv`: 3 categories (enrolled, spoof, unknown) × 4 distances
(2, 3, 4, 5 m) × 20 = **240 trials**, in 20 rounds. Each round has all 12
combinations once, in a shuffled order (fixed seed 2026, so the order can be
reproduced). Balanced rounds mean tiredness or a change in lighting during
the session affects every combination about equally. Part 1 = rounds 1–10,
Part 2 = rounds 11–20. Expect "Not detected" at 5 m — those are real results
and count in the success rates.

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

## Group test (3–5 people at once)

How fast the gate lets in **several enrolled people standing in front of it
together**. Each face gets its own frame vote (the frames must agree per
face, not across the whole picture), so people are let in one after another
as their own frames agree, not one at a time.

**Before:** 3 to 5 enrolled people (registered with face photos), a line on
the floor **3 m** from the camera lens, and an **extra person at the laptop**
pressing Start, out of the camera's view.

**Session form:** **Test type → Group**, then:

- **Members' ID numbers**, in order, separated by spaces or commas. The first
  is **Member 1**, the second **Member 2**, and so on. With 5 members the
  plan has groups of 3, 4 and 5; with 4, groups of 3 and 4.
- **Trials per group size**: 10 for the full test, which makes 30 trials with
  5 members.
- **Timeout**: 15 s. A group needs longer than one person.

The group test has its own files (`group_plan.csv`, `group_results.csv`),
so it never mixes with the single-person test's results. There's no need to
press "Start a new test" first.

**The plan:** 10 rounds, each with every group size once, in a shuffled
order (fixed seed). Each trial says which members step up. Everyone takes
part an equal number of times (24 each with 5 members), so no one person's
face decides the result.

**Each trial:**

1. The window shows e.g. **GROUP OF 4** and the members who step up
   ("Member 1: …  ·  Member 3: …").
2. Those members stand **side by side on the 3 m line, close together**
   (shoulder to shoulder), **facing away**. The others stay out of view.
3. Wait for **READY**; the laptop person presses **Start (F8)**; the members
   turn to the camera **together** and stand still.
4. The trial ends when the **last** member is let in, or at the timeout.

Redo and Skip work as in the single test. Use Redo, for example, when
someone turned late or a non-member walked into view.

**What is measured** (from the first usable face's frame, like the single
test):

- **group_decision_time_ms**: the main measure, until the **last** member
  was let in. It is filled in only when everyone was let in.
- **member_decision_ms**: each member's own time, in the order listed in
  `members` ("-" = never let in). **first_member_ms** is the fastest one.
- **result**: "All in", or "k of n in" when the timeout came first.
  **correct** = everyone was let in *and* nobody else was matched.
- **wrong_matches**: the ID of anyone matched who wasn't in this group
  (should stay empty). **unknown_events** / **spoof_events**: Unknown or
  Spoof entries written during the trial. **card_request_frames**: frames
  that asked for a card tap.
- **faces_max**: the most faces seen in one picture (a check that everyone
  was in view). **repeat_entries**: members let in again within the
  repeat-entry cooldown (still a valid time, as in the single test).
- the same speed columns as the single test (answer, backend, face AI,
  freezes, CPU / memory).

The summary script (below) also writes `group_summary_table.csv` (per group
size: all-in rate with a 95% CI, members let in, the group time's mean /
median / SD, and each member's median time) and `group_time_vs_size.png`.

## The files

In `entry-agent\test-results\` (an installed copy: its data folder; the
**Open results folder** button opens it):

- `results.csv` — one row per trial (see the columns below);
- `trial_plan.csv` — the plan;
- `group_results.csv`, `group_plan.csv` — the group test's results and plan;
- `session_<date-time>_settings.json` — every Settings value, the model
  setup, the camera and gate monitor settings for that session (for the
  paper's "materials" section).

Columns of `results.csv`: trial, round, session_id, date_time, category,
distance_m, subject_label, spoof_medium, time_to_first_detection_ms,
decision_time_ms, decision_time_from_first_face_ms, frames_processed,
frames_with_face, frames_skipped_turned / _moving / _blur / _edge,
frames_covered, frames_face_beyond_limit, frames_failed_fake_check (frames
the subject's face failed the fake-face check on), frames_match_held (frames
a match was held back because of such a failure - Settings, "A failed check
holds the match"), result, expected_result, correct,
repeat_entry, matched_name, matched_id, match_confidence, best_similarity,
liveness_score, liveness_min (the lowest fake-face score in the trial),
liveness_model_27, liveness_model_40, liveness_classical
(the fake-face score's parts: each trained model's own "real" score and the
texture cues - for comparing the models and tuning the strictness; a face
estimated closer than 2.5 m is scored with the 2.7 model only, both are
still recorded), flat_judged_frames, flat_frames, flat_score_min,
shape_change_max (a watch-only "flat face" measure, recorded but never used
to decide: on how many frames the face had turned enough to judge, on how
many it moved like a flat sheet - a held photo - its lowest score, and the
face's largest change of shape; see backend users/flatness.py),
face_width_px, face_height_px, estimated_distance_m,
frame_size_sent, camera_resolution, note, status (final / redone).

## Speed — when the gate feels slow

Every trial also records where each frame's time went, and the test window
shows it after each trial:

- **capture** — taking and encoding the picture on the gate laptop;
- **answer** — from sending the picture until its result is ready (network +
  backend + building the result); **answer max** is the slowest frame;
- **backend** (server) — the backend's own part of the answer, of which
  **face AI** is finding faces and their fingerprints, and **fake check** the
  liveness model;
- **screen freezes** — times the gate monitor's window didn't update for
  0.5 s or more, the longest one, and how busy the computer was (CPU % and
  memory %).

Every freeze and every answer slower than 1.5 s is also written, with the
time and the trial number, to `performance_log.csv` in the results folder.
For a slow answer, `backend_ms` is the backend's own part of it; the rest was
spent between the programs. (Setup C's slow answers were that: the gate
monitor connected to "localhost", and on Windows every new connection to it
waited ~2 s - it now connects to 127.0.0.1.)

How to read it: a big **answer** with a small **backend** = the time is lost
on the gate laptop or between the programs; a big **backend** with a small
**face AI** = the database or other checks; a big **face AI** = the processor
is too slow for the face models. Freezes with memory near 90–100 % = the
laptop is short of memory (close the browser and other programs); with CPU
near 100 % = it's overloaded (plug it in, power mode "Best performance").

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
- `decision_time_vs_distance.png`, `success_rate_vs_distance.png`;
- if `group_results.csv` is there too: `group_summary_table.csv` and
  `group_time_vs_size.png` (see Group test). Give it the path of a
  `group_results.csv` to summarize only the group test.

Success rate counts every trial, so Not detected / No decision / Occluded
lower it. For spoof and unknown, the table also gives a **kept-out rate**:
trials that did not end in a Match, i.e. the face didn't let them in, even
if it wasn't labelled exactly right (a spoof read as Unknown). Report the
strict success rate as the main result, and the kept-out rate next to it. Decision-time figures use only the trials that reached a decision.

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
> [printed photo] of the enrolled user) and an unregistered person — at 2,
> 3, 4 and 5 m from the camera lens, 20 trials per combination (240 trials),
> in 20 balanced, randomized rounds [run in two sessions of 10 rounds each,
> each containing half of every combination]. In each trial the subject faced away from the
> camera, the trial was started, and the subject turned to face the camera.
> Decision time was measured with a high-resolution monotonic clock from the
> capture of the first frame in which the face passed the system's quality
> checks to the system's final decision (agreement of 2 of the last 4 frames);
> a trial with no decision within 10 s was recorded as not detected (no face
> seen) or no decision. A trial was correct if the enrolled user was matched
> to their own record, the attack was rejected as a spoof, or the
> unregistered person was rejected as unknown. Success rates are reported
> with 95% Wilson confidence intervals.
