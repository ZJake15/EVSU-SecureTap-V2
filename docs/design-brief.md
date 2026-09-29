# EVSU SecureTap — UI Redesign Brief

**For:** an AI UI designer producing high-fidelity mockups.
**Redesign scope:** the web **Dashboard**, the Windows **Gate Monitor** (entry-agent), and the Windows **Launcher**.
**Attach with this brief:** `docs/EVSU_Logo.png` (the university seal).

Read §3 (hard rules) before anything else. Those rules override every other suggestion in this document. Every screen, field, status, and piece of wording in §9 and §10 is real and must appear in the mockups. Do not invent features, metrics, or placeholder text.

---

## 1. The product

EVSU SecureTap is the campus gate entry system for **Eastern Visayas State University** (Tacloban City, Philippines, founded 1907).

- A camera at the gate watches continuously and identifies everyone in frame by face, several people at once, CCTV-style. Nobody stops and poses.
- Every face is checked for **liveness**, so a printed photo or a phone screen held up to the camera is caught as a spoof.
- An **NFC ID card tap** is the second credential. It confirms ambiguous face matches ("tap card to confirm"), and it's always required for people on record as lookalikes (for example, identical twins).
- Every entry and exit is logged. Staff review everything in a web dashboard.

There are three surfaces to design:

| Surface | Technology | Who looks at it | Where |
|---|---|---|---|
| **Dashboard** | Web app (React + Tailwind) | Admin, SASO, Security Officers | Office desktops, 1280–1920 px wide |
| **Gate Monitor** | Windows desktop app (Python CustomTkinter) | The guard on duty, all shift | A 22–27" monitor in the gate booth, viewed from 1–3 m, often at a glance |
| **Launcher** | Windows desktop app (Python CustomTkinter) | Whoever starts the system | The same PC, a small window at startup |

## 2. Users and roles

| Role | What they do | Dashboard access |
|---|---|---|
| **Admin** | IT / system administrator | Everything. Approves deactivation requests, manages dashboard accounts, views read-only system settings. |
| **SASO** (security manager) | Enrolls and edits students and staff | Live Monitoring, Logs, User Management, Reports, Audit Log (own actions only). Can only *request* a deactivation, which an Admin must approve. |
| **Security Officer** | Guard supervisor for one gate | Live Monitoring and Logs for **their assigned gate, today only**. No export. Can log a **manual override** when the scanner fails. |
| **Guard at the gate** | Watches the Gate Monitor | Not a dashboard user. Reads the screen while also watching people. The student walking through may see the screen too. |

The conditions are demanding. The gate booth has glare and changing daylight, the guard glances at the screen mid-conversation, and alarm moments must be unmistakable while quiet moments stay calm.

## 3. Hard rules (non-negotiable)

### Typography
1. The primary brand/display typeface must **not** be Inter, Roboto, system-ui, SF Pro, Segoe UI, or any OS default.
2. **Also not Fraunces.** The current dashboard pairs Fraunces with Inter, and that look is being retired.
3. The hierarchy must have **high contrast**. Use at least three levels that differ in size **and** weight or width. Page titles and hero numerals should be at least 2.5× the body size. Do not set everything semibold.

### Color
4. **No purple-to-indigo gradients.** No purple or indigo anywhere at all. Today's dashboard uses purple for "spoof suspected", and that goes too.
5. **No warm cream, parchment, beige, or off-white page backgrounds.** The current page background is `#FAF7F2` "parchment" with a faint concentric-ring texture. Both are being retired.
6. **Strict token palette.** Every color in every mockup must be one of the 17 tokens in §5. Do not use ad-hoc hex values, default Tailwind colors (`emerald-500`, `sky-500`, and so on), or the multicolor avatar backgrounds used today.

### Layout
7. **No identical uniform card grids.** The current dashboard has a row of 6 equal stat boxes and an 8-column wall of identical event cards. The current Gate Monitor's live log is a 4-column grid of identical cards. Replace all of these by varying scale, span, and density to express importance.
8. **Asymmetric spacing rhythm** (§7). Do not reuse one gap value everywhere. Column splits must be unequal (for example 8/4 or 7/5, never 6/6 or 4/4/4).

### Other AI-design tropes to avoid
- Glassmorphism, frosted blur, neon glow, gradient meshes, 3D blobs, floating orbs, "sparkle" AI icons.
- **Emoji as icons.** Today's UI uses 🤚 ⚠ 📷. On Windows these render as full-color emoji that clash with the palette. Use one real icon set (§8).
- "Badge soup," where every status is a pastel pill with a border.
- A soft drop shadow on every card, or everything rounded into big pills.
- A "cyber-security hacker" aesthetic: green-on-black terminals, scan lines, HUD corner brackets, radar sweeps.
- Stock illustrations, generic hero art, and random-color placeholder avatars.
- Lorem ipsum, "99.9% uptime"-style filler metrics, or any feature not described here.

## 4. Brand

- **The seal** (`docs/EVSU_Logo.png`) is a maroon disc with a gold torch flame, a green map of the Eastern Visayas islands, a white open book, the ring text "EASTERN VISAYAS STATE UNIVERSITY", and "1907". Use it **unaltered**: no recoloring, cropping, outlining, or redrawn simplified versions. Keep it at least 32 px, with clear space of at least a quarter of its diameter.
- **Wordmark.** Design an "EVSU SecureTap" wordmark in the display typeface to sit beside the seal.
- **Colors.** Maroon comes from the seal's disc. The brass accent comes from the torch flame, muted so it reads as institutional brass rather than bright yellow. The seal's green is **not** a UI color.
- **Character.** Think of a civic operations system: transit control-room signage, airport departure boards, a well-run government ID office. It is institutional, calm, legible, and trustworthy. It is not a startup landing page and not a sci-fi security console.

## 5. Color tokens — the only 17 colors allowed

Neutrals are **cool**, not warm. That is deliberate, to stay away from cream and parchment.

| Token | Hex | Role |
|---|---|---|
| `ink-950` | `#121416` | Primary text. Dark surfaces: the video letterbox, console/log panels, optionally a dark nav rail. |
| `ink-600` | `#4B5157` | Secondary text, the neutral "checking…" status. |
| `ink-400` | `#8A9097` | Placeholder and disabled text, tertiary meta. **Large text or non-text only** (about 3.2:1 on white). |
| `line` | `#D9DCDF` | Hairlines, table rules, input borders, dividers. |
| `canvas` | `#EEF0F2` | App background (cool light gray). |
| `surface` | `#FFFFFF` | Panels, tables, inputs, drawers. |
| `maroon` | `#7B1113` | Brand. Primary buttons, active navigation, header bands, links. |
| `maroon-deep` | `#4A0A0C` | Pressed states, dark brand surfaces (e.g. the Launcher header, a nav rail). |
| `brass` | `#C89B3C` | Accent, used sparingly: the active indicator line, rules, and emphasis on dark backgrounds. **Never for text on white** (about 2.5:1). On `maroon`, only for non-text or text of 20 px and up (about 4.3:1). On `ink-950` it's fine for any text (about 7:1). Under 3% of any screen. |
| `verified` | `#1E7B45` | Matched person, entry/exit granted, card accepted, "system OK". |
| `verified-tint` | `#E3F2E9` | Background companion for `verified`. |
| `caution` | `#9A5B00` | Unknown person, needs review, offline or queued taps, warnings. |
| `caution-tint` | `#FBEFD9` | Background companion for `caution`. |
| `danger` | `#C62828` | Spoof suspected, card rejected, errors, alarms, destructive actions. |
| `danger-tint` | `#FBE4E4` | Background companion for `danger`. |
| `prompt` | `#1D5FA8` | Action needed **from the person at the gate**: "Tap card to confirm", "Lookalike check", "Please uncover your face". Also the keyboard focus ring. |
| `prompt-tint` | `#E2ECF7` | Background companion for `prompt`. |

Rules:
- **Every text/background pair meets WCAG 2.2 AA.** White text works on `maroon`, `maroon-deep`, `ink-950`, `ink-600`, `verified`, `caution`, `danger`, and `prompt`. Colored text works on `surface` and on its own tint.
- **`danger` must never be confused with `maroon`.** Danger is brighter and appears only where something is wrong or destructive. Maroon is the brand and never means "alarm".
- Tints are for backgrounds only, never for text.
- If a screen seems to need another color, you've used too many. Remove one rather than add one.
- **Charts** use tokens only. Series order: `maroon`, `ink-600`, `prompt`, `verified`, `caution`, `danger`. Gridlines use `line`, axis text uses `ink-600`.

## 6. Typography

Recommended system (all SIL Open Font License: free on Google Fonts, and embeddable in the Windows app as TTF files). You may substitute, but only within §3's rules and with a one-line justification.

| Family | Role | Why |
|---|---|---|
| **Archivo** (variable: width 62–125, weight 100–900) | Display: wordmark, page titles, hero numerals, section titles, uppercase labels | One family gives two strongly contrasting textures. The **Expanded / SemiExpanded** widths at heavy weight read as authoritative signage. The **Condensed** width fits dense uppercase labels and column headers. |
| **Atkinson Hyperlegible Next** | Body text, tables, forms, names | Designed by the Braille Institute so easily-confused characters (I/l/1, O/0, rn/m) stay distinct. That matters when a guard reads a name or ID in a glance. |
| **IBM Plex Mono** | IDs (`2021-00123`), NFC IDs, timestamps, percentages, thresholds | Tabular figures keep columns aligned and stop live-updating numbers from jittering. |

**Type scale (px), deliberately non-uniform:** 12 · 14 · 16 · 20 · 28 · 44 · 72

| Role | Size / weight | Face |
|---|---|---|
| Hero numeral (key metrics, gate-monitor stats) | 72 (or 56 in tight spots) / 800 | Archivo Expanded |
| Page title | 44 / 800, tracking −1% | Archivo SemiExpanded |
| Section title | 20 / 700 | Archivo SemiExpanded |
| Eyebrow / column header / label | 12 / 700, UPPERCASE, tracking +8% | Archivo Condensed |
| Body | 16 / 400 | Atkinson Hyperlegible Next |
| Dense table body | 14 / 400 | Atkinson Hyperlegible Next |
| IDs, times, numbers in text | 14 or 12 / 500 | IBM Plex Mono |

**Gate Monitor minimums** (read from 1–3 m):
- Person names: at least 28 px.
- Status words: at least 20 px.
- Stat numerals: at least 56 px.
- Nothing below 14 px.

**Windows app constraint:** Tk can't use variable-font axes, so every weight/width you use must exist as a **static TTF** in the Google Fonts download (for example `Archivo_SemiExpanded-ExtraBold.ttf`). List the exact static files you used in the style sheet.

## 7. Spacing, grid, and shape

**Spacing tokens (px), roughly 1.6× steps, not an even grid:** 4 · 6 · 10 · 16 · 26 · 42 · 68 · 110

**Rhythm rules:**
- Distance expresses relationship:
  - Label ↔ its value: 4–6
  - Items within a group: 10–16
  - Between groups: 26–42
  - Between major page sections: 68
- **Asymmetric vertical rhythm.** Space above a section heading is larger than the space below it (e.g. 42 above, 16 below). The page's top margin is larger than its bottom margin.
- **Asymmetric columns.** Use a 12-column grid. Primary/secondary splits are 8/4 or 7/5, never 6/6 or 4/4/4.
- **Proximity before boxes.** Group things with space, alignment, and hairlines before reaching for a bordered card. Not every group needs a container.
- **Density contrast.** Dense zones (tables with 40 px rows, 14 px text) sit next to generous headers and hero numerals. Contrast in density is part of the hierarchy.

**Radii (only three):** `r-sm` 3 px (inputs, chips, table badges), `r-md` 8 px (panels, drawers, buttons), `r-lg` 14 px (the video panel). No full pill shapes except one allowed exception: the Gate Monitor's ENTRY/EXIT direction badge.

**Elevation (only two levels):** in-page elements are flat, with no shadows on panels or cards. One elevated layer (a drawer, dialog, or dropdown) may use one soft shadow.

## 8. Shared components and the status vocabulary

The same status must look the same in the Dashboard and the Gate Monitor. Today they disagree (spoof is purple in one and red in the other), so fix that.

**Every status is icon + word + color, never color alone.** Use **one icon set: Phosphor Icons** (MIT), in Regular and Bold weights, at sizes 16 / 20 / 28. For the Windows app, specify them as PNG exports.

| Status | Token | Suggested icon | Wording |
|---|---|---|---|
| Face match, entry | `verified` | `sign-in` | ENTRY |
| Face match, exit | `verified` | `sign-out` | EXIT |
| Card accepted | `verified` | `identification-card` | CARD · ENTRY / CARD · EXIT |
| Unknown person (confirmed) | `caution` | `user-circle-dashed` | UNKNOWN |
| Needs review, lookalike unresolved | `caution` | `users-three` | NEEDS REVIEW |
| Spoof suspected | `danger` | `warning-octagon` | SPOOF |
| Card rejected | `danger` | `identification-card` + `x` | CARD REJECTED · *reason* |
| Tap card to confirm | `prompt` | `identification-card` | TAP CARD TO CONFIRM |
| Lookalike check | `prompt` | `users-three` | TAP CARD · LOOKALIKE CHECK |
| Please uncover your face | `prompt` | `hand-palm` | PLEASE UNCOVER YOUR FACE |
| Checking | `ink-600` | `circle-notch` | Checking… / Face the camera / Move into view / Hold steady |
| Manual override | `ink-950` + `brass` rule | `hand-pointing` | MANUAL · *username* |
| Occlusion detected (**old records only**) | `ink-400` | `hand-palm` | Face covered (historical) |

**Other components to define once and reuse:**
- **Buttons:** primary (maroon fill), secondary (surface + line), quiet (text only), destructive (danger outline). Height 40 on the Dashboard, 44 on the Gate Monitor and Launcher.
- **Inputs:** text, select, date range, and search. A segmented control (Entry / Exit), a checkbox, and a dropdown (camera picker).
- **Tables:** sticky header with condensed uppercase labels, mono IDs and times, and a status-marker column. Security rows (spoof, unknown) get a 3 px left edge bar in their token, not a full-row tint.
- **Drawer** (Dashboard, right side, about 560 px wide) for adding or editing a person.
- **Notices:** inline notices (info / caution / danger) and a small toast for "saved" confirmations.
- **Empty, loading, and error states** for every data view.

## 9. Screens

### 9A. Dashboard (web). Design at 1440 × 900; it must also hold at 1280 and 1920.

**App shell.** Today it's a maroon top bar with seal, wordmark, and subtitle "Campus Entry Monitoring", up to 7 text links, the role label, and a Log out button. Redesign freely (a left rail is fine) but keep:
- **Role-filtered navigation:**
  - Admin sees: Live Monitoring, Logs, User Management, Reports, Audit Log, Accounts, Settings.
  - SASO sees: all of those except Accounts and Settings.
  - Security Officer sees: Live Monitoring and Logs only.
- **The signed-in role**, plus the Security Officer's assigned gate (e.g. "Security Officer · Main Gate", or "no gate assigned").
- **Log out.**

**1. Login.** Seal, wordmark, "Campus Entry Monitoring System", Username, Password, Sign in, and an inline error ("Invalid username or password."). Today's login is a generic gray page and is fully replaceable.

**2. Live Monitoring.** The most important dashboard screen. It updates every second with today's events.
- **Metrics:** Passes today · Enrolled matches · Unknown attempts · Spoof suspected · Average confidence (%). *No "occlusion" metric*: covered faces are no longer recorded.
- **Each event:** a photo (enrolled reference photo, or the captured face crop for an unknown person, or initials if neither), name or "Unknown", Student/Employee ID or "No ID on file", time, method marker, and match confidence % (for matches).
- **Method markers:** Face · Face + card · Lookalike + card · Spoof suspected · Flagged (unknown) · Needs review (lookalike unresolved) · Card (historical).
- **Lookalike events** also show the person's **distinguishing note** (e.g. "mole on left cheek, wears glasses").
- **Suggested direction:** make the newest event big. Pin anything needing attention (spoof, unknown, needs review) in its own narrower column or zone, and show everything else as a dense chronological list. Metrics become one hero number plus secondary figures, *not* equal boxes.
- **States:** waiting for the first scan of the day; "Live feed unavailable — retrying…"; the Security Officer view (their gate, today only, with the gate name shown in the page header).

**3. Logs.** The full history.
- **Filters:** date range, name search, gate, status (All / Success / Failed / Spoof suspected / Occlusion detected [historical]), and a "Hide unknown" toggle.
- **Columns:** Name · Timestamp · Direction · Method · Status · Confidence · Reason · Gate.
- **Method values:** Face · Face + card · Lookalike tiebreak · Card · **Manual override · *username*** (always shows who).
- **Pagination:** 25 per page, with "Export CSV" (Admin and SASO only).
- **Security Officer variant:** no export and no date filter. It adds a **Manual override** form with Student/Employee ID, Direction (Entry/Exit), and Reason ("What did you check? e.g. checked physical school ID"), plus a Submit button.

**4. User Management** (Admin and SASO).
- **People table:** Name · Role (Student/Staff) · ID · NFC ID · Department/course · Status (Active/Inactive), plus row actions (Edit, Deactivate for Admin or "Request deactivation" for SASO).
- **Row badges:** Lookalike pair · Pending deactivation · Single-photo enrollment (lower confidence).
- **Add/Edit drawer:**
  - Full name, Role, Student/Employee ID, NFC ID ("Tap a card to fill this in"), Department/course.
  - Optional profile picture (JPEG/PNG/HEIC only).
  - Optional distinguishing note ("visible to security staff, never used for matching").
  - Face enrollment:
    - **Guided 5-shot capture from the webcam:** Front ("Straight on") · Slight left · Slight right · Neutral ("No expression") · Smile ("Natural smile"). Each slot shows live preview → captured thumbnail → quality check pass/fail with a reason (too blurry / no face / more than one face / face too small).
    - **Single-photo fallback**, which marks the enrollment lower-confidence.
  - Existing enrollment photos, e.g. "3 / 5", or up to 8 for people in a lookalike pair.
- **Pending deactivation requests** (Admin only): person, requested by, reason, date, Approve / Reject (with an optional note). A SASO requesting deactivation must give a reason.
- **Lookalike (confusable) pairs panel:**
  - Each pair: Person A ↔ Person B, plus its source. Auto-detected pairs show a similarity score (e.g. "0.63"); manual pairs show "flagged by *username*". Each pair has a Remove action.
  - A form to flag two people as a pair.
- **Bulk import:** upload a CSV/XLSX, then see per-row results (Imported / Error with reason / Lookalike warning).

**5. Reports** (Admin and SASO).
- **Range:** Today / Last 7 days / Last 30 days.
- **Stats:** Entries today · Failed verifications today · Busiest hour today.
- **Charts:**
  - Entries by method (stacked: Face / Face + card / Failed; plus an "Occluded" segment that exists only in historical data).
  - Confidence score distribution (histogram).
  - Busiest hours (0–23).
  - Entries per day (last 7 days).
  - False-accept / false-reject rate vs. match threshold (a two-line chart). This one must carry a visible **"Preliminary estimate"** caveat explaining it's computed from enrolled photos, not an independent test set.

**6. Audit Log** (Admin sees all; SASO sees only their own actions).
- **Columns:** Timestamp · Actor · Action · Target · Detail.
- **Action vocabulary:** Account created / updated / deactivated · Deactivation requested / approved / rejected · Manual override logged · Person deleted permanently · Lookalike pair flagged / removed.

**7. Accounts** (Admin only).
- **Table:** Username · Name · Role · Gate · Status.
- **Form:** Username, Password (when editing: "Leave blank to keep current password"), Full name, Role (Admin / Security Manager (SASO) / Security Officer), Assigned gate (only for Security Officer, e.g. "Main Gate"), Active.

**8. Settings** (Admin only, **read-only**).
- A clear "Read-only for now" notice: values come from the server config, and changing them means editing it and restarting.
- **Groups:**
  - Face matching: match threshold 0.45, tiebreak margin 0.05, tiebreak timeout 10 s.
  - Liveness: score threshold 0.5.
  - Voting window: size 4, agreement 2, window 4 s.
  - Gate-scan quality: detector size 480, minimum blur 25.0, edge margin 0.02, max yaw 0.35.
  - Occlusion detection: mode in effect "classifier", classifier cutoff 0.5, plus three rule thresholds (0.73 / 0.40 / 0.65).
  - Cooldowns: recognition 60 s, unenrolled capture 30 s, spoof capture 30 s.
  - Enrollment: max photos 5 (8 for lookalike pairs).
- All values are monospace.

### 9B. Gate Monitor (Windows desktop). Design at 1920 × 1080 (opens maximized); it must hold down to 820 × 680.

**Current structure:**
- Header band: "Main Gate — live monitoring", subtitle "Face recognition + NFC card", live clock, ENTRY/EXIT direction badge.
- Stats strip: Today · Entries · Unknown · Spoof · In frame. It wraps to rows of 3 on narrow windows.
- Main area: **left 40%** holds the live camera and, below it, a compact card-scanner strip. **Right 60%** holds the live log, currently a 4-column grid of identical cards, which must go.
- Status bar: "Recognition threshold 0.45", Camera OK/not, a camera picker dropdown ("0: Integrated Webcam"), guard name · app version on the left; Backend OK/not, "Queue 0", "Synced" on the right.

You may rebalance proportions (the camera arguably deserves more space), but keep all of this information.

**Live camera panel:**
- A 16:9 video, letterboxed on `ink-950`, with the `r-lg` corner radius.
- A small "LIVE" indicator, and a caption such as "1280×720 · 2 faces tracked".
- **Face boxes:** a 2 px outline in the status token, with a **solid** label tab attached to the box. Tabs read, for example: "Maria Clara D. Santos · 92%", "UNKNOWN", "SPOOF", "PLEASE UNCOVER YOUR FACE", "TAP CARD TO CONFIRM", "TAP CARD · LOOKALIKE CHECK", "Checking…", "Face the camera", "Move into view", "Hold steady".
- **Alarm banner:** appears only for a newly confirmed Unknown person or Spoof, together with an alarm sound, and auto-hides after 6 s. Design it so it **never covers faces**, for example docked to the top edge of the video or rendered as a band outside it.
- **A covered face gets only its label tab.** No banner, no sound, nothing counted, nothing logged. This is deliberate.
- **No-camera state:** "No camera connected — card taps still work. Connecting a camera resumes automatically."

**Card scanner** (a compact strip). Its states:
1. **Waiting:** "Tap a card" / "Hold ID near the reader".
2. **Checking:** "Checking card…".
3. **Accepted:** 76 px photo, name, role, course/department, Student ID, card ID, direction, time, and the distinguishing note if the person has one.
4. **Rejected:** a headline of "Not registered" / "Deactivated" / "Read error", or "Offline — tap queued, will sync automatically".

It returns to Waiting after 8 s.

**Live log:**
- Newest first, up to 60 events.
- Event types: face ENTRY/EXIT, UNKNOWN, SPOOF, CARD · ENTRY/EXIT, CARD REJECTED.
- Each event shows a thumbnail (reference photo, captured face crop, or initials), the name, and a meta line with ID · time.
- Replace the uniform grid: for example, feature the latest event large, then show a dense list, with security events visually distinct.

**Scenario frames to show:** see §12.

### 9C. Launcher (Windows desktop). The default window is 560 × 760 and resizable; when maximized, the content caps at about 960 px and centers.

- **Header:** seal, wordmark, "Choose what to open".
- **Service status for Backend / Dashboard / Entry agent**, each with a state: not running · starting · ready · failed, plus a short message ("Backend stopped — open the log to see why").
- **Two choices** (side by side at 700 px or wider, stacked below that):
  - **Dashboard** — "Web app — register users, live monitoring, logs, reports".
  - **Entry Agent** — "Gate monitor — camera face recognition + NFC card reader".
  - Each shows progress while starting ("Starting the camera and opening the gate monitor…"), a running state ("Gate monitor is open"), and a failure state ("Failed to start — open the log below to see why").
- **Notices** (only when relevant):
  - "Last session — Main Gate (entry): 42 entries, 5 exits, 2 unknown — ended Sep 20, 04:15 PM".
  - A caution notice: "3 NFC taps haven't synced to the server yet — they'll retry automatically once the entry agent is running and online."
- **"Entry Agent settings"** (collapsed by default): Gate (text), Direction (Entry / Exit segmented control), Guard name (optional), and "Automatically open the gate monitor when this launcher starts" (checkbox).
- **"Show log"** (collapsed by default): a monospace console of process output.
- **Footer:** "Quitting stops everything this window started. · v1.0" and a Quit button.
- **Pre-flight warning dialog**, titled "Before you open the gate monitor". It can list "The backend isn't responding yet — entry/exit logging won't work until it is." and "No ACR122U NFC reader was detected (best-effort check)." It always ends with "The gate monitor will still open."

## 10. Sample content (use this, not lorem ipsum)

All names are fictional.

**People:**

| Name | Role | ID | NFC ID | Dept/course |
|---|---|---|---|---|
| Maria Clara D. Santos | Student | 2021-00123 | 0A3F5C21 | BSIT |
| Juan Miguel R. Dela Cruz | Student | 2022-04417 | 1B7E90D4 | BSCE |
| Kristine Joy B. Ramos | Student | 2023-01288 | 3C21A7F0 | BSEd – Mathematics |
| Mark Anthony L. Villanueva | Student | 2021-03350 | 4D88B2E1 | BSEE |
| Rhea Mae C. Bacalso | Student | 2024-00096 | 5E14C3A9 | BSHM |
| Angelica P. Uy | Staff | EMP-0412 | 6F02D8B7 | Registrar's Office |
| Ramon T. Abad | Staff | EMP-0178 | 7A93E4C6 | Security Office |

Lookalike pair: Paolo S. Mendoza ↔ Paulo S. Mendoza (twins), auto-detected at similarity 0.63. Note: "Paolo has a mole on his left cheek."

**Gates:** Main Gate, Back Gate.

**Formats:**
- Times in Asia/Manila, 12-hour with seconds (07:42:18 AM). Dates like Sep 27, 2026.
- Confidence 56–94%. Match threshold 45%.

**Dashboard accounts:** `admin` (System Administrator, Admin), `saso.reyes` (Liza M. Reyes, SASO), `guard.maingate` (Noel B. Cortez, Security Officer, Main Gate).

**Audit samples:**
- "saso.reyes · Deactivation requested · Person #58 (Mark Anthony L. Villanueva) · reason: transferred out".
- "admin · Deactivation approved · Person #58".
- "guard.maingate · Manual override logged · 2022-04417 · checked physical school ID".

**Reports samples:** 412 entries today, 9 failed verifications, busiest hour 7:00 AM. The busiest-hours curve peaks at 7 AM and 4–5 PM.

**Gate Monitor stats (Main Gate, ENTRY):** Today 412 · Entries 398 · Unknown 7 · Spoof 1 · In frame 2. Guard: Noel B. Cortez · v1.0. Camera: "0: Integrated Webcam".

## 11. Platform constraints (keep mockups buildable)

**Dashboard (React 19, Tailwind CSS 3, Recharts):**
- Fonts via Google Fonts, icons as SVG.
- Tokens become CSS variables and a Tailwind theme extension.
- Anything CSS can do is fine, within §3.

**Gate Monitor and Launcher (Python CustomTkinter/Tk on Windows).** Design only what this toolkit can draw.
- **Can:** solid fills; rounded rectangles (per-widget corner radius); 1–2 px borders; PNG images; text in bundled static TTF fonts; drawing on the video canvas (rectangles, rounded shapes, text); simple timed state changes; color-change hovers.
- **Cannot:** transparency or translucent overlays (over video or anywhere); blur; real drop shadows (at most a 1 px darker offset frame); CSS-style gradients (only as pre-rendered images, used sparingly); mixed styles within one text label; animated transitions; variable-font axes; emoji.
- **Video overlays must be opaque.** Label tabs are solid fills attached to face boxes, and the alarm banner is solid.
- **Windows display scaling.** Check layouts at 100% and 125% (125% multiplies every size by 1.25), and leave slack.

**Both:**
- WCAG 2.2 AA contrast.
- Color is never the only signal.
- Keyboard focus is visible on the Dashboard, using the `prompt` token.

## 12. Deliverables

**A. Style sheet (one page):**
- Token swatches with names and hex values.
- Type specimen for every role in §6, plus the list of static TTF files the Windows app needs.
- Spacing scale, radii, and elevation.
- The icon list.
- The full status-marker set from §8.
- Buttons, inputs, segmented control, table row, drawer, and notices.

**B. Gate Monitor (1920 × 1080):**
1. Calm state: two recognized people entering, recent log.
2. **Alarm:** an unknown person, with the banner showing.
3. **Alarm:** spoof suspected.
4. Prompt: covered face (label only) next to a recognized person.
5. Prompt: "Tap card to confirm", and the card scanner showing an accepted card.
6. Card rejected ("Not registered").
7. No camera connected.
8. Offline: backend not OK, "Queue 3".
9. The calm state at 1100 × 700 (narrow).

**C. Dashboard (1440 × 900):**
1. Login.
2. Live Monitoring, busy, with attention items.
3. Live Monitoring, empty ("waiting for the first scan").
4. Live Monitoring, Security Officer view.
5. Logs, Admin view.
6. Logs, Security Officer view with the manual override form.
7. User Management list.
8. Add Person drawer, mid guided capture (2 of 5 captured, 1 failing quality check).
9. Lookalike pairs panel and pending deactivation queue.
10. Reports.
11. Audit Log.
12. Accounts.
13. Settings.

**D. Launcher:**
1. 560 × 760: backend starting.
2. 560 × 760: everything ready, with the last-session notice and the unsynced-taps notice.
3. Entry Agent settings expanded.
4. Maximized at 1920 × 1080: content centered, cards side by side.
5. The pre-flight warning dialog.

**If time is limited,** do them in this order: B2, B1, C2, A, D2, C5, C8, C10, then the rest.

## 13. What must stay the same (functional behavior)

- Every piece of information in §9 stays available. It can move, regroup, or collapse, but nothing is dropped.
- Role visibility exactly as in §2 and §9A. A Security Officer only ever sees their own gate, today only.
- A manual override is always visibly labeled as such, with the username.
- **Covered faces:** a prompt label on the face only. Never counted, logged, bannered, or sounded.
- **Alarm banner and sound:** only for a newly confirmed unknown person or spoof.
- The seal appears unaltered.

## 14. Acceptance checklist (verify before handing off)

- [ ] Primary typeface isn't Inter, Roboto, system-ui, an OS default, or Fraunces.
- [ ] No purple, indigo, or purple-to-indigo gradients anywhere.
- [ ] No cream, parchment, or beige backgrounds; `canvas` is `#EEF0F2`.
- [ ] Every color used is one of the 17 tokens in §5; charts included.
- [ ] No identical uniform card grids: stats, events, and the live log all vary in scale or density.
- [ ] Spacing uses the §7 scale with asymmetric rhythm; column splits are unequal.
- [ ] Type hierarchy: page titles and hero numerals are at least 2.5× body size, with at least 3 distinct levels.
- [ ] Every status shows icon + word + color, identically in the Dashboard and Gate Monitor.
- [ ] No emoji anywhere; one icon set (Phosphor).
- [ ] Gate Monitor text meets the minimums in §6; the alarm banner never covers faces.
- [ ] Nothing in the Gate Monitor or Launcher uses transparency, blur, shadows, or gradients (§11).
- [ ] All sample content comes from §10; no lorem ipsum, no invented features.
- [ ] WCAG 2.2 AA contrast on every text/background pair.
