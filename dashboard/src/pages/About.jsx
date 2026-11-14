import { useEffect, useState } from "react";
import apiClient from "../api/client";
import logo from "../assets/logo.png";
import { useAuth } from "../auth/AuthContext";
import { Icon, PageHeader, Segmented } from "../components/ui";
import PrintableForms from "./PrintableForms";

// About: what EVSU SecureTap is, the university it serves (from
// https://www.evsu.edu.ph/ - its history, vision, mission, core values and
// contacts), and - for Admin and SASO - the printable privacy forms.
// /about#university and /about#forms open those tabs (the old /forms
// address opens the forms).

const FEATURES = [
  ["scan-smiley", "Face recognition at the gate", "A camera checks every face that walks up and matches it against the registered students and staff - several people at once, each with their own decision."],
  ["shield-warning", "Fake-face check", "Printed photos and phone screens are caught by an anti-spoofing check before a face is ever matched."],
  ["identification-card", "School ID card", "A card tap confirms an unsure or lookalike match, and works on its own when the camera can't be used."],
  ["graduation-cap", "Semester enrollment", "Students not on this semester's enrolled list are flagged NOT ENROLLED - or refused, if the university chooses."],
  ["broadcast", "Live monitoring and logs", "Every entry, unknown face, spoof attempt and covered face appears on the dashboard within a second, with reports and an audit log."],
  ["lock-key", "Privacy by design", "Face data never leaves the SecureTap computer, no video is recorded, and every staff action is written to the audit log (Data Privacy Act of 2012)."],
];

const HISTORY = [
  ["1907", "Begins as part of the Provincial School"],
  ["1915", "Becomes a separate school - the Leyte Trade School"],
  ["1953", "Renamed the National Provincial Trade School (R.A. 406)"],
  ["1961", "Becomes the Leyte Regional Arts and Trades (R.A. 1516)"],
  ["1965", "Chartered as the Leyte Institute of Technology (R.A. 4572)"],
  ["2004", "Converted into Eastern Visayas State University (R.A. 9311)"],
];

const CORE_VALUES = [
  ["E", "Excellence"],
  ["V", "Virtue"],
  ["S", "Service"],
  ["U", "Unity"],
];

const CAMPUSES = ["Tacloban City (main campus)", "Burauen", "Carigara", "Dulag", "Ormoc", "Tanauan"];

function Section({ title, children }) {
  return (
    <section className="card flex flex-col gap-s4 p-s5">
      <h2 className="t-section">{title}</h2>
      {children}
    </section>
  );
}

function SystemTab() {
  const [health, setHealth] = useState(null);
  useEffect(() => {
    apiClient
      .get("/health")
      .then(({ data }) => setHealth(data))
      .catch(() => setHealth(null));
  }, []);

  return (
    <div className="flex flex-col gap-s5">
      <div className="card flex flex-wrap items-center gap-s5 p-s5">
        <img src={logo} alt="EVSU seal" className="h-20 w-20 flex-none" />
        <div className="flex min-w-0 flex-1 flex-col gap-s2">
          <span className="font-display stretch-semi text-[28px] font-extrabold leading-tight">EVSU SecureTap</span>
          <span className="text-base text-ink-600">
            Campus entry monitoring for Eastern Visayas State University - face recognition with a school ID card as
            backup, built as a capstone project.
          </span>
        </div>
        <div className="flex flex-col gap-s1 text-sm text-ink-600">
          <span>
            Server:{" "}
            <span className={`font-bold ${health ? "text-verified" : "text-caution"}`}>
              {health ? "running" : "not reachable"}
            </span>
          </span>
          {health?.api_version != null && <span className="font-mono">API version {health.api_version}</span>}
        </div>
      </div>

      <Section title="What it does">
        <div className="grid gap-s4 md:grid-cols-2 xl:grid-cols-3">
          {FEATURES.map(([icon, title, text]) => (
            <div key={title} className="flex gap-s3">
              <span className="flex h-10 w-10 flex-none items-center justify-center rounded-md bg-maroon text-white">
                <Icon name={icon} bold size={20} />
              </span>
              <div className="flex flex-col gap-s1">
                <span className="font-bold">{title}</span>
                <span className="text-sm leading-snug text-ink-600">{text}</span>
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section title="How a gate works">
        <ol className="flex flex-col gap-s3 text-sm leading-snug">
          {[
            "The gate computer's camera sends a picture to the SecureTap server several times a second.",
            "The server finds every face, checks it isn't a photo or screen, and compares it with the registered faces.",
            "A face is only let in when several pictures in a row agree - one lucky frame is never enough.",
            "The guard's screen shows the result on each face; unsure or lookalike matches are asked for a card tap.",
            "Every decision is logged and appears on this dashboard straight away.",
          ].map((step, index) => (
            <li key={step} className="flex gap-s3">
              <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-canvas font-mono text-xs font-bold">
                {index + 1}
              </span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      </Section>
    </div>
  );
}

function UniversityTab() {
  return (
    <div className="flex flex-col gap-s5">
      <div className="card flex flex-wrap items-center gap-s5 bg-maroon-deep p-s5 text-white">
        <img src={logo} alt="EVSU seal" className="h-20 w-20 flex-none" />
        <div className="flex min-w-0 flex-1 flex-col gap-s1">
          <span className="font-display stretch-condensed text-xs font-bold uppercase tracking-[0.08em] text-brass">
            Est. 1907 · Tacloban City
          </span>
          <span className="font-display stretch-semi text-[28px] font-extrabold leading-tight">
            Eastern Visayas State University
          </span>
          <span className="text-sm text-line">Archbishop Lino R. Gonzaga Avenue, Tacloban City, Philippines 6500</span>
        </div>
      </div>

      <div className="grid gap-s5 lg:grid-cols-2">
        <Section title="Vision">
          <p className="font-display stretch-semi text-xl font-bold leading-snug">
            &ldquo;A premier institution of learning in the ASEAN region by 2040.&rdquo;
          </p>
        </Section>
        <Section title="Mission">
          <p className="font-display stretch-semi text-xl font-bold leading-snug">
            &ldquo;Develop competent and productive professionals with positive values for sustainable development.&rdquo;
          </p>
        </Section>
      </div>

      <Section title="Core values">
        <div className="grid grid-cols-2 gap-s3 md:grid-cols-4">
          {CORE_VALUES.map(([letter, word]) => (
            <div key={letter} className="flex items-center gap-s3 rounded-md border border-line p-s3">
              <span className="font-display stretch-wide text-[36px] font-black leading-none text-maroon">{letter}</span>
              <span className="font-bold">{word}</span>
            </div>
          ))}
        </div>
      </Section>

      <div className="grid gap-s5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Section title="History">
          <ol className="flex flex-col">
            {HISTORY.map(([year, text]) => (
              <li key={year} className="flex gap-s4 border-l-2 border-brass py-s2 pl-s4">
                <span className="w-12 flex-none font-mono font-bold text-maroon">{year}</span>
                <span className="text-sm leading-snug">{text}</span>
              </li>
            ))}
          </ol>
        </Section>
        <div className="flex flex-col gap-s5">
          <Section title="Campuses">
            <ul className="flex flex-col gap-s1 text-sm">
              {CAMPUSES.map((campus) => (
                <li key={campus} className="flex items-center gap-s2">
                  <Icon name="map-pin" size={14} className="text-maroon" />
                  {campus}
                </li>
              ))}
            </ul>
          </Section>
          <Section title="Contact">
            <ul className="flex flex-col gap-s2 text-sm">
              <li className="flex items-center gap-s2">
                <Icon name="phone" size={14} className="text-maroon" /> Telefax <span className="font-mono">+63 53 888 2565</span>
              </li>
              <li className="flex items-center gap-s2">
                <Icon name="users" size={14} className="text-maroon" /> Student Affairs Office{" "}
                <span className="font-mono">saso.sao@evsu.edu.ph</span>
              </li>
              <li className="flex items-center gap-s2">
                <Icon name="globe" size={14} className="text-maroon" />
                <a className="link-action" href="https://www.evsu.edu.ph/" target="_blank" rel="noreferrer">
                  www.evsu.edu.ph
                </a>
              </li>
            </ul>
          </Section>
        </div>
      </div>
      <p className="text-xs text-ink-600">
        From the university&apos;s website, www.evsu.edu.ph. University President: Dr. Dennis C. de Paz.
      </p>
    </div>
  );
}

export default function About() {
  const { user } = useAuth();
  const canPrint = user?.role === "admin" || user?.role === "saso";
  const tabs = [
    { value: "system", label: "EVSU SecureTap", icon: "shield-check" },
    { value: "university", label: "The university", icon: "graduation-cap" },
    ...(canPrint ? [{ value: "forms", label: "Printable forms", icon: "printer" }] : []),
  ];
  const [tab, setTab] = useState(() => {
    const wanted = window.location.hash.slice(1);
    return tabs.some((t) => t.value === wanted) ? wanted : "system";
  });
  const choose = (value) => {
    setTab(value);
    window.history.replaceState(null, "", `#${value}`);
  };

  return (
    <div className="flex flex-col gap-s5">
      <div className="print:hidden">
        <PageHeader eyebrow="About" title="About">
          <Segmented value={tab} onChange={choose} options={tabs} />
        </PageHeader>
      </div>
      {tab === "system" && <SystemTab />}
      {tab === "university" && <UniversityTab />}
      {tab === "forms" && canPrint && <PrintableForms embedded />}
    </div>
  );
}
