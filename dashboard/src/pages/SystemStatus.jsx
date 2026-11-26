import { useCallback, useEffect, useState } from "react";
import apiClient from "../api/client";
import { Icon, Notice, PageHeader } from "../components/ui";

// System Status (Admin and SASO): is every gate monitor running, its
// cameras, card taps waiting offline, who's on duty - and the server's last
// backup, free disk space and database size, with what needs attention on
// top. Backend: logs/office_views.py SystemStatusView. Each gate monitor
// reports about once a minute, so this refreshes every 30 seconds.

const REFRESH_MS = 30000;

function bytes(value) {
  if (value == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function when(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function ago(value) {
  if (!value) return "";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 90) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

function StatusPill({ ok, okText, badText }) {
  return (
    <span
      className={`inline-flex items-center gap-s1 rounded-sm px-s2 py-[2px] text-xs font-bold ${
        ok ? "bg-verified-tint text-verified" : "bg-danger-tint text-danger"
      }`}
    >
      <Icon name={ok ? "check-circle" : "x-circle"} bold size={14} />
      {ok ? okText : badText}
    </span>
  );
}

function Row({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-s4 border-t border-line py-s2 text-sm first:border-t-0">
      <span className="flex-none text-ink-600">{label}</span>
      <span className="min-w-0 text-right text-ink">{children}</span>
    </div>
  );
}

function Camera({ camera, label }) {
  if (!camera) return <Row label={label}>none</Row>;
  return (
    <Row label={label}>
      <StatusPill ok={camera.online} okText="Sending" badText="Not sending" />{" "}
      <span className="text-ink-600">
        {[camera.camera, camera.resolution, camera.fps ? `${Math.round(camera.fps)} fps` : null]
          .filter(Boolean)
          .join(" · ")}
      </span>
    </Row>
  );
}

function GateCard({ gate, signInEnabled }) {
  return (
    <div className="card flex flex-col gap-s3 p-s5">
      <div className="flex items-start justify-between gap-s3">
        <div className="flex min-w-0 flex-col gap-s1">
          <span className="t-section truncate">{gate.gate}</span>
          <span className="text-xs text-ink-600">
            {gate.online
              ? `Gate monitor running · reported ${ago(gate.last_seen)}`
              : `Last heard from ${when(gate.last_seen)}`}
          </span>
        </div>
        <StatusPill ok={gate.online} okText="Running" badText="Not running" />
      </div>
      <div className="flex flex-col">
        <Camera camera={gate.entrance} label="Entrance camera" />
        <Camera camera={gate.exit} label="Exit camera" />
        {signInEnabled && (
          <Row label="On duty">
            {gate.on_duty ? (
              <b>{gate.on_duty.name}</b>
            ) : (
              <span className="font-bold text-caution">Nobody signed in</span>
            )}
          </Row>
        )}
        <Row label="Today">
          <span className="font-mono">
            {gate.entries_today} in · {gate.exits_today} out
          </span>
        </Row>
        <Row label="Card taps waiting (offline)">
          <span className={`font-mono ${gate.queue ? "font-bold text-caution" : ""}`}>{gate.queue ?? "—"}</span>
        </Row>
        <Row label="Last card tap">{gate.last_tap ? when(gate.last_tap) : "—"}</Row>
        <Row label="Gate monitor version">
          <span className="font-mono">{gate.app_version || "—"}</span>
        </Row>
      </div>
    </div>
  );
}

export default function SystemStatus() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    apiClient
      .get("/system-status")
      .then(({ data: status }) => {
        setData(status);
        setError("");
      })
      .catch(() => setError("Couldn't reach the server - showing the last answer."));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const server = data?.server;
  const backup = server?.last_backup;
  const backupLate = !backup || backup.days_ago > (server?.backup_due_days ?? 7);
  const disk = server?.disk;

  return (
    <div className="flex flex-col gap-s5">
      <PageHeader eyebrow={data ? `Checked ${when(data.server_time)}` : "Checking…"} title="System Status">
        <button type="button" className="btn-secondary" onClick={load}>
          <Icon name="arrow-clockwise" size={16} /> Check again
        </button>
      </PageHeader>

      {error && <Notice tone="caution">{error}</Notice>}

      {data && (
        <div className="flex flex-col gap-s2">
          {data.warnings.length === 0 ? (
            <Notice tone="verified">Everything looks fine.</Notice>
          ) : (
            data.warnings.map((warning, index) => (
              <Notice key={index} tone={warning.level === "danger" ? "danger" : "caution"}>
                {warning.text}
              </Notice>
            ))
          )}
        </div>
      )}

      {data && (
        <section className="flex flex-col gap-s3">
          <h2 className="t-eyebrow">Gates</h2>
          {data.gates.length === 0 ? (
            <p className="text-sm text-ink-600">No gate monitor has reported in the last week.</p>
          ) : (
            <div className="grid gap-s4 md:grid-cols-2 2xl:grid-cols-3">
              {data.gates.map((gate) => (
                <GateCard key={gate.gate} gate={gate} signInEnabled={data.sign_in_enabled} />
              ))}
            </div>
          )}
        </section>
      )}

      {server && (
        <section className="flex flex-col gap-s3">
          <h2 className="t-eyebrow">Server</h2>
          <div className="card grid gap-x-s6 p-s5 md:grid-cols-2">
            <div className="flex flex-col">
              <Row label="Last backup">
                {backup ? (
                  <span className={backupLate ? "font-bold text-caution" : ""}>
                    {when(backup.at)} ({backup.days_ago === 0 ? "today" : `${backup.days_ago} days ago`})
                    {backup.by && <span className="font-normal text-ink-600"> by {backup.by}</span>}
                  </span>
                ) : (
                  <span className="font-bold text-caution">Never</span>
                )}
              </Row>
              <Row label="How to back up">
                <span className="text-ink-600">&quot;Back up data&quot; in the SecureTap launcher</span>
              </Row>
              <Row label="Database">
                {server.database.engine}
                {server.database.size != null && <span className="font-mono"> · {bytes(server.database.size)}</span>}
              </Row>
            </div>
            <div className="flex flex-col">
              <Row label="Free disk space">
                {disk ? (
                  <span className="font-mono">
                    {bytes(disk.free)} of {bytes(disk.total)}
                  </span>
                ) : (
                  "—"
                )}
              </Row>
              <Row label="People registered">
                <span className="font-mono">{server.people}</span>
              </Row>
              <Row label="With face photos">
                <span className="font-mono">{server.enrolled_people}</span>
              </Row>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
