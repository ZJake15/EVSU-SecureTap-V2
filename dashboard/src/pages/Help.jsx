import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Icon, PageHeader } from "../components/ui";

// Help: short how-to guides for each page, shown only for the pages this
// account's role can open, plus the gate monitor and troubleshooting.

const ALL = ["admin", "saso", "security_officer"];
const MANAGERS = ["admin", "saso"];

const GUIDES = [
  {
    key: "start", icon: "sign-in", title: "Getting started", roles: ALL,
    steps: [
      "Sign in with the account the Admin gave you. Admins and SASO see every gate; a Security Officer sees only their own assigned gate, today only.",
      "The menu on the left only shows the pages your role can use.",
      "If the dashboard is left idle for the time set in Settings, it signs you out by itself - sign in again to continue.",
      "Always use Log out at the bottom of the menu on a shared computer.",
    ],
  },
  {
    key: "live", icon: "broadcast", title: "Live Monitoring", to: "/", roles: ALL,
    steps: [
      "Updates every second. The top row counts today's scans: enrolled matches, unknown attempts, spoof suspected, face covered and not enrolled.",
      "The table lists today's successful entries, newest first, with how each was confirmed (Face, Face + card, Card, Manual).",
      "Needs attention pins everything a guard should look at: SPOOF SUSPECTED (a photo or screen), FLAGGED · UNKNOWN (a face nobody is registered as), FACE COVERED, NOT ENROLLED (let in or refused) and NEEDS REVIEW (a lookalike that never tapped a card).",
      "Admin and SASO also see who is on duty at each gate.",
    ],
  },
  {
    key: "cameras", icon: "video-camera", title: "Cameras", to: "/cameras", roles: ["admin"],
    steps: [
      "Every gate's camera, live. A gate only sends its picture while this page is open - nothing is recorded.",
      "Show face boxes draws the gate's own results on the video (name, UNKNOWN, SPOOF...).",
      "Enlarge shows one gate alone; the corner button goes full screen. \"No signal\" means that gate's monitor isn't open.",
    ],
  },
  {
    key: "logs", icon: "list-bullets", title: "Logs", to: "/logs", roles: ALL,
    steps: [
      "Every entry and refusal, 25 per page. Filter by date, name, gate and status (including Spoof suspected, Face covered and Not enrolled).",
      "Export CSV downloads the rows shown (Admin and SASO).",
      "Security Officers: when the scanner can't confirm someone, check their school ID yourself and use Manual override to record the entry - it's logged under your account.",
    ],
  },
  {
    key: "users", icon: "users", title: "User Management", to: "/users", roles: MANAGERS,
    steps: [
      "Register a student or staff member with Add person: their details, school ID card number and the guided face photos (front, slight left, slight right, neutral, smile).",
      "Have them sign the consent form first (About › Printable forms).",
      "The Bulk import tab adds many people at once from a CSV or Excel file.",
      "If two people look alike, the system flags them as a confusable pair - they're then asked for a card tap at the gate.",
      "SASO requests a deactivation with a reason; it only takes effect once an Admin approves it.",
    ],
  },
  {
    key: "semester", icon: "graduation-cap", title: "Semester enrollment", to: "/settings#semester", roles: MANAGERS,
    steps: [
      "Each semester, open Settings › Semester Enrollment and choose Start new semester with the registrar's list of enrolled students (CSV or Excel).",
      "Pick the column with the student ID number - the page suggests the one that matches the most registered students.",
      "For late enrollees, upload an updated list or use Mark enrolled beside their name - it takes effect at the gate at once.",
      "An Admin decides there whether a student who isn't enrolled is let in with a NOT ENROLLED warning or refused, and can set a grace period.",
    ],
  },
  {
    key: "reports", icon: "chart-bar", title: "Reports", to: "/reports", roles: MANAGERS,
    steps: [
      "Today, last 7 or last 30 days: entries, unknown attempts, the busiest hours, every outcome by method, and the confidence of matches.",
      "Export CSV saves the by-method numbers for a spreadsheet.",
    ],
  },
  {
    key: "audit", icon: "clipboard-text", title: "Audit Log", to: "/audit-log", roles: MANAGERS,
    steps: [
      "Every staff action that matters: account changes, deactivations, manual overrides, setting changes, semester lists, gate sign-ins and camera viewing.",
      "Admins see everyone's actions; SASO sees their own.",
    ],
  },
  {
    key: "accounts", icon: "user-gear", title: "Accounts", to: "/accounts", roles: ["admin"],
    steps: [
      "Create dashboard accounts for Admins, SASO and Security Officers. A Security Officer needs an assigned gate.",
      "For signing in at the gate monitor: tap the guard's own staff ID card into Staff ID card, and type their employee ID in Staff record for face sign-in - their face photos in User Management are what the camera checks.",
      "Deactivate an account when someone leaves - they can no longer sign in, and their past actions stay in the Audit Log.",
    ],
  },
  {
    key: "settings", icon: "gear-six", title: "Settings", to: "/settings", roles: ["admin"],
    steps: [
      "Every setting has a plain description and, for the risky ones, a recommended range - a change outside it asks you to confirm.",
      "Changes reach every gate within a few seconds; nobody restarts anything. Every change is in the Audit Log.",
      "Use Reset this group or Reset all to go back to the defaults; nothing is saved until you click Save changes.",
    ],
  },
  {
    key: "gate", icon: "scan-smiley", title: "At the gate (gate monitor)", roles: ALL,
    steps: [
      "Green box with a name: let in. Amber UNKNOWN: not registered - two bell notes, high-low, twice. Red SPOOF: a photo or screen - a rising siren, three times.",
      "Blue PLEASE UNCOVER YOUR FACE: a mask, cap or hand is in the way - two soft notes, once. TAP CARD TO CONFIRM: an unsure or lookalike match - ask for the school ID card.",
      "NOT ENROLLED: the student isn't on this semester's list - amber if let in (no sound), red if refused (a falling \"uh-oh\" buzz). Send them to the Student Affairs Office.",
      "Each sound can be switched off in Settings › Alerts.",
      "A gate with an exit camera shows ENTRANCE and EXIT side by side. Exits never stop anyone; an unknown or spoof at the exit still sounds its alarm.",
      "NO ENTRY TODAY or NO EXIT YET on a live-log row: the person's day doesn't add up - usually a missed scan. Let them through and ask.",
      "To set up the cameras or the exit card reader: Cameras & card readers in the status bar. For the reader, click Set exit reader, then tap any card on the exit reader.",
      "The Layout button in the status bar picks how the screen looks: Split (camera + live log), Verify wall (photo on file next to the live face), Full camera, Filmstrip, Entrance + Exit (both cameras, side by side or big + small), Compact rail (small laptop screens), or Separate window (the exit camera on a second monitor). It's remembered on that computer.",
      "Card taps show as a pill on the camera (Tap a card / Card accepted / Card not registered). NOT ENROLLED, a person's note and \"photos due\" show on their newest live-log card.",
      "If the camera fails, students can still tap their school ID card on the reader.",
      "Signing in for duty: the sign-in window shows the ways Settings › Security & Accounts allows - password, staff ID card, face scan. With Card, then face, tap your card first, then look at the camera inside the oval until it signs you in.",
    ],
  },
];

const TROUBLE = [
  ["The gate shows \"Offline\"", "The gate computer can't reach the SecureTap server. Check that the server computer is on and both are on the same network; card taps are queued and sent when it's back."],
  ["A registered student isn't recognized", "Ask them to face the camera, stand still and remove caps or masks. If it keeps happening, retake their face photos in User Management."],
  ["A real student is flagged SPOOF", "Strong backlight or glare can cause it. Check the lighting at the gate; an Admin can adjust Settings › Anti-Spoofing."],
  ["A student says they're enrolled but shows NOT ENROLLED", "Check Settings › Semester Enrollment: search their name under \"Registered, not on the list\" and Mark enrolled if they enrolled late."],
  ["A camera shows \"No signal\" on the Cameras page", "That gate's monitor isn't open, or the gate computer is off."],
  ["Forgot your password", "Ask an Admin to set a temporary one in Accounts - it also unlocks a locked account. Sign in with it, then choose your own; nothing else opens until you do. At the gate the temporary password doesn't work - use your staff ID card or face."],
  ["An Admin forgot their own password", "Another Admin resets it in Accounts - keep two Admin accounts for this. With no other Admin: on the server computer, in the EVSU SecureTap folder, run python\\python.exe backend\\manage.py changepassword <username>."],
];

function Guide({ guide, open, onToggle }) {
  return (
    <section className="card overflow-hidden">
      <button type="button" onClick={onToggle} aria-expanded={open}
              className="flex w-full items-center gap-s3 px-s5 py-s4 text-left hover:bg-canvas">
        <span className="flex h-9 w-9 flex-none items-center justify-center rounded-md bg-maroon text-white">
          <Icon name={guide.icon} bold size={18} />
        </span>
        <span className="t-section flex-1">{guide.title}</span>
        <Icon name={open ? "caret-up" : "caret-down"} bold size={16} className="text-ink-600" />
      </button>
      {open && (
        <div className="flex flex-col gap-s3 border-t border-line px-s5 py-s4">
          <ol className="flex flex-col gap-s2 text-sm leading-snug">
            {guide.steps.map((step, index) => (
              <li key={step} className="flex gap-s3">
                <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-canvas font-mono text-xs font-bold">
                  {index + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
          {guide.to && (
            <Link to={guide.to} className="link-action self-start">
              Open {guide.title} <Icon name="arrow-right" size={14} />
            </Link>
          )}
        </div>
      )}
    </section>
  );
}

export default function Help() {
  const { user } = useAuth();
  const guides = GUIDES.filter((guide) => guide.roles.includes(user?.role));
  const [open, setOpen] = useState(() => new Set(["start"]));
  const [search, setSearch] = useState("");
  const query = search.trim().toLowerCase();
  const shown = query
    ? guides.filter((g) => [g.title, ...g.steps].some((text) => text.toLowerCase().includes(query)))
    : guides;
  const toggle = (key) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="flex flex-col gap-s5">
      <PageHeader eyebrow="How to use EVSU SecureTap" title="Help">
        <span className="relative block w-[280px] max-w-full">
          <Icon name="magnifying-glass" size={16} className="pointer-events-none absolute left-[10px] top-3 text-ink-600" />
          <input className="input pl-9" placeholder="Search help" value={search}
                 onChange={(event) => setSearch(event.target.value)} />
        </span>
      </PageHeader>

      <div className="grid items-start gap-s5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-s3">
          {shown.map((guide) => (
            <Guide key={guide.key} guide={guide} open={Boolean(query) || open.has(guide.key)}
                   onToggle={() => toggle(guide.key)} />
          ))}
          {shown.length === 0 && <p className="text-sm text-ink-600">Nothing in the guides mentions that.</p>}
        </div>

        <div className="flex flex-col gap-s3">
          <span className="t-section">Troubleshooting</span>
          {TROUBLE.map(([problem, answer]) => (
            <div key={problem} className="card flex flex-col gap-s1 p-s4">
              <span className="font-bold">{problem}</span>
              <span className="text-sm leading-snug text-ink-600">{answer}</span>
            </div>
          ))}
          <div className="card flex flex-col gap-s1 bg-canvas p-s4 text-sm">
            <span className="font-bold">Still stuck?</span>
            <span className="text-ink-600">
              Ask your SecureTap Admin, or the Student Affairs Office (saso.sao@evsu.edu.ph) for questions about a
              student&apos;s record.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
