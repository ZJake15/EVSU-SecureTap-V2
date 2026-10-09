import { useEffect, useState } from "react";
import logo from "../assets/logo.png";
import { useAuth } from "../auth/AuthContext";
import { Field, Icon, Notice, PageHeader, Segmented } from "../components/ui";

// The two papers the privacy policy (installer/PRIVACY-POLICY.txt, sections
// 13 and 18) says must exist before SecureTap is used at a real gate: the
// written consent form signed before someone's face is registered, and the
// notice posted where people see it before they reach the camera. Each is
// one A4 page, printed with the browser's own Print (the menu and the
// fill-in fields aren't printed - see the print: classes and index.css).
// The retention periods come from the Settings page, so the paper says what
// the system actually does.

const FIELDS_KEY = "securetap_printable_forms";
const DEFAULT_FIELDS = {
  contact: "the EVSU Data Protection Officer, through the university's official channels",
  office: "Office of Student Affairs and Services (SASO)",
  gate: "",
  alternative: "show your school ID to the guard, who records your entry by hand",
};

function loadFields() {
  try {
    return { ...DEFAULT_FIELDS, ...JSON.parse(localStorage.getItem(FIELDS_KEY) || "{}") };
  } catch {
    return DEFAULT_FIELDS;
  }
}

// "kept for 365 days" or, while Automatic deletion is off, the honest
// version - nothing is deleted on a schedule yet.
function keptFor(days, policy) {
  if (!policy) return "kept for the period the university sets";
  if (!policy.auto_delete_enabled) return "kept until the university deletes them";
  return `kept for ${days} days, then deleted automatically`;
}

function Line({ label }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="h-7 border-b border-ink" />
      <span className="text-[11px] text-ink-600">{label}</span>
    </div>
  );
}

function Box({ children }) {
  return (
    <div className="flex items-start gap-s3">
      <span className="mt-0.5 h-4 w-4 flex-none border-[1.5px] border-ink" />
      <span>{children}</span>
    </div>
  );
}

function SheetHeader({ title, subtitle }) {
  return (
    <div className="flex items-center gap-s4 border-b-[3px] border-maroon pb-s3">
      <img src={logo} alt="" className="h-14 w-14 flex-none" />
      <div className="flex min-w-0 flex-col">
        <span className="font-display stretch-condensed text-xs font-bold uppercase tracking-[0.08em] text-ink-600">
          Eastern Visayas State University &middot; EVSU SecureTap
        </span>
        <span className="font-display stretch-semi text-2xl font-extrabold leading-tight text-ink">{title}</span>
        {subtitle && <span className="text-sm text-ink-600">{subtitle}</span>}
      </div>
    </div>
  );
}

function ConsentForm({ fields, policy }) {
  const records = keptFor(policy?.keep_entry_records_days, policy);
  return (
    <div className="flex flex-col gap-s3 text-[12.5px] leading-snug text-ink">
      <SheetHeader title="Consent to face registration" subtitle="Campus entry monitoring - Data Privacy Act of 2012 (RA 10173)" />

      <section className="flex flex-col gap-s2">
        <h2 className="font-display stretch-semi text-sm font-bold">What this is</h2>
        <p>
          EVSU SecureTap checks who enters and leaves the campus gates. A camera at the gate recognizes registered
          students and staff by their face, a school ID card can be tapped instead, and each entry and exit is recorded.
          This form asks whether you agree to have <strong>your face registered</strong> for this.
        </p>
      </section>

      <section className="grid grid-cols-2 gap-x-s5 gap-y-s2">
        <div className="flex flex-col gap-s2">
          <h2 className="font-display stretch-semi text-sm font-bold">If you agree, SecureTap keeps</h2>
          <ul className="list-disc space-y-0.5 pl-5">
            <li>your name, ID number, course or department and ID card number</li>
            <li>up to 5 photos of your face, taken by staff with you present</li>
            <li>a &ldquo;face fingerprint&rdquo; made from each photo - numbers that describe your face; the photo
              can&rsquo;t be rebuilt from it</li>
            <li>a record of each time you enter or leave: date, time, gate and how you were checked</li>
          </ul>
        </div>
        <div className="flex flex-col gap-s2">
          <h2 className="font-display stretch-semi text-sm font-bold">How it is used and kept</h2>
          <ul className="list-disc space-y-0.5 pl-5">
            <li>only for campus security - never for grades, advertising or anything else, and never sold</li>
            <li>stored only on the SecureTap computer, not on the internet; no video is recorded</li>
            <li>seen only by authorized staff (Admin, SASO and guards, each limited to what their work needs)</li>
            <li>your photos and face data are kept while you are registered and deleted when you leave or withdraw;
              entry records are {records}</li>
          </ul>
        </div>
      </section>

      <section className="flex flex-col gap-s2">
        <h2 className="font-display stretch-semi text-sm font-bold">Your choice and your rights</h2>
        <ul className="list-disc space-y-0.5 pl-5">
          <li><strong>Saying no is allowed</strong> and has no effect on your grades or standing. If you don&rsquo;t
            agree, you can still enter: {fields.alternative}.</li>
          <li>You can <strong>withdraw</strong> your consent at any time by telling the {fields.office}. Your face
            photos and face data are then deleted.</li>
          <li>You have the right to be informed, to see and get a copy of your data, to have it corrected or
            deleted, to object, and to complain to the National Privacy Commission (privacy.gov.ph).</li>
          <li>Questions and requests: {fields.contact}.</li>
        </ul>
      </section>

      <section className="flex flex-col gap-s2 rounded-sm border border-ink p-s3">
        <h2 className="font-display stretch-semi text-sm font-bold">Student or employee</h2>
        <div className="grid grid-cols-2 gap-x-s5 gap-y-s2">
          <Line label="Full name" />
          <Line label="Student or employee ID number" />
          <Line label="Course / year, or department" />
          <Line label="School ID card number (if known)" />
        </div>
        <div className="mt-s2 flex flex-col gap-s2">
          <Box>
            <strong>I agree</strong> to have my face registered in EVSU SecureTap and used as described above.
          </Box>
          <Box>
            <strong>I do not agree.</strong> I will use another way to enter campus.
          </Box>
        </div>
        <div className="grid grid-cols-2 gap-x-s5">
          <Line label="Signature" />
          <Line label="Date" />
        </div>
      </section>

      <section className="flex flex-col gap-s2 rounded-sm border border-ink p-s3">
        <h2 className="font-display stretch-semi text-sm font-bold">
          Parent or guardian <span className="font-normal text-ink-600">- only if the student is under 18</span>
        </h2>
        <p>I am the parent or legal guardian of the student above and I agree to the choice marked above.</p>
        <div className="grid grid-cols-3 gap-x-s5">
          <Line label="Name and relationship" />
          <Line label="Signature" />
          <Line label="Date" />
        </div>
      </section>

      <section className="grid grid-cols-3 gap-x-s5 border-t border-dashed border-ink-400 pt-s2 text-ink-600">
        <span className="col-span-3 text-[11px] font-bold uppercase tracking-[0.08em]">For office use</span>
        <Line label="Registered by" />
        <Line label="Date registered" />
        <Line label="Form kept at" />
      </section>
    </div>
  );
}

function GateNotice({ fields, policy }) {
  const photos = keptFor(policy?.keep_gate_photos_days, policy);
  const points = [
    ["users-three", "A camera here checks the face of everyone who passes - students, staff and visitors."],
    ["identification-card", "Registered students and staff are recognized by face or school ID card. Every entry and exit is recorded."],
    ["user-circle-dashed", `If you are not registered, a photo of your face may be kept so the guard can see who was at the gate. Your face data is deleted after 1 day; these photos are ${photos}.`],
    ["video-camera-slash", "No video is recorded. Nothing is sent to the internet."],
    ["shield-check", "Used only for campus security, under the Data Privacy Act of 2012 (RA 10173)."],
    ["hand-palm", `Don't want face recognition? Tell the guard - you can ${fields.alternative}.`],
  ];
  return (
    <div className="flex flex-1 flex-col gap-s5 text-ink">
      <SheetHeader title={fields.gate || "Campus gate"} />
      <div className="flex flex-col items-center gap-s3 rounded-md bg-maroon px-s5 py-s5 text-center text-white">
        <Icon name="scan-smiley" bold size={64} />
        <span className="font-display stretch-wide text-[44px] font-black leading-none tracking-[0.02em]">
          FACE RECOGNITION IN USE
        </span>
        <span className="text-lg">This gate uses EVSU SecureTap to check who enters and leaves the campus.</span>
      </div>
      <ul className="flex flex-col gap-s4">
        {points.map(([icon, text]) => (
          <li key={icon} className="flex items-start gap-s4 text-[19px] leading-snug">
            <Icon name={icon} bold size={30} className="mt-0.5 text-maroon" />
            <span>{text}</span>
          </li>
        ))}
      </ul>
      <div className="mt-auto flex flex-col gap-s1 border-t-[3px] border-maroon pt-s3 text-base">
        <span><strong>Questions, or to see, correct or delete your data:</strong> {fields.contact}.</span>
        <span className="text-ink-600">The full privacy policy is available from the {fields.office}.</span>
      </div>
    </div>
  );
}

export default function PrintableForms() {
  const { policy } = useAuth();
  const [doc, setDoc] = useState("consent");
  const [fields, setFields] = useState(loadFields);

  useEffect(() => {
    try {
      localStorage.setItem(FIELDS_KEY, JSON.stringify(fields));
    } catch {
      // only a convenience - the fields just reset next time
    }
  }, [fields]);

  const set = (key) => (event) => setFields((current) => ({ ...current, [key]: event.target.value }));

  return (
    <div className="flex flex-col gap-s5">
      <div className="print:hidden">
        <PageHeader eyebrow="Privacy" title="Printable forms">
          <Segmented
            value={doc}
            onChange={setDoc}
            options={[
              { value: "consent", label: "Consent form", icon: "signature" },
              { value: "notice", label: "Gate notice", icon: "sign-in" },
            ]}
          />
          <button type="button" className="btn-primary" onClick={() => window.print()}>
            <Icon name="printer" bold size={18} /> Print
          </button>
        </PageHeader>
      </div>

      <div className="card flex flex-col gap-s4 p-s5 print:hidden">
        <p className="text-sm text-ink-600">
          {doc === "consent"
            ? "Have each student or employee sign this before their face is registered, and keep the signed forms as the university requires. Print one per person."
            : "Post this at each gate where people can read it before they reach the camera."}{" "}
          The wording below fills in from these fields and from the Settings page&rsquo;s retention periods.
        </p>
        <div className="grid gap-s4 md:grid-cols-2">
          <Field label="Who to contact with questions and requests">
            <input className="input" value={fields.contact} onChange={set("contact")} />
          </Field>
          <Field label="Office that handles withdrawals">
            <input className="input" value={fields.office} onChange={set("office")} />
          </Field>
          <Field label="Another way in, for people who say no" hint={'Finishes the sentence "you can ..."'}>
            <input className="input" value={fields.alternative} onChange={set("alternative")} />
          </Field>
          {doc === "notice" && (
            <Field label="Gate name (shown at the top of the notice)">
              <input className="input" value={fields.gate} onChange={set("gate")} placeholder="Main Gate" />
            </Field>
          )}
        </div>
        {policy && !policy.auto_delete_enabled && (
          <Notice tone="caution">
            <strong>Automatic deletion is off</strong>, so records and gate photos are kept until someone deletes them,
            and that&rsquo;s what the paper says. An Admin can turn it on in Settings &gt; Privacy &amp; Data Retention -
            the paper then shows the periods set there.
          </Notice>
        )}
        <button type="button" className="link-action self-start" onClick={() => setFields(DEFAULT_FIELDS)}>
          Reset the fields
        </button>
      </div>

      <div className="print-sheet mx-auto flex min-h-[1060px] w-full max-w-[794px] flex-col rounded-sm border border-line bg-surface p-s6">
        {doc === "consent" ? <ConsentForm fields={fields} policy={policy} /> : <GateNotice fields={fields} policy={policy} />}
      </div>
    </div>
  );
}
