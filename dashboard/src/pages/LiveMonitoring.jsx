import { useEffect, useRef, useState } from "react";
import apiClient from "../api/client";

const POLL_INTERVAL_MS = 1000;
const MAX_EVENTS_SHOWN = 500;

function startOfToday() {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  return date.toISOString();
}

function EnrollmentBadge({ status }) {
  const isEnrolled = status === "success";
  return (
    <span
      className={`inline-flex w-full items-center justify-center rounded-full px-2 py-1.5 text-sm font-bold ${
        isEnrolled ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
      }`}
    >
      {isEnrolled ? "Enrolled" : "Not Enrolled"}
    </span>
  );
}

export default function LiveMonitoring() {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const sinceRef = useRef(startOfToday());

  useEffect(() => {
    let isCancelled = false;

    const poll = async () => {
      try {
        const { data } = await apiClient.get("/logs/live", {
          params: { since: sinceRef.current },
        });
        if (isCancelled) return;
        if (data.results?.length) {
          setEvents((prev) => [...data.results, ...prev].slice(0, MAX_EVENTS_SHOWN));
          sinceRef.current = data.results[0].timestamp;
        }
        setError("");
      } catch {
        if (!isCancelled) setError("Live feed unavailable - retrying...");
      }
    };

    poll();
    const intervalId = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      isCancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  const enrolledCount = events.filter((event) => event.status === "success").length;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">Live Monitoring - Today's Gate Activity</h1>
        <span className="text-sm text-gray-600">
          {events.length} pass{events.length === 1 ? "" : "es"} today ({enrolledCount} enrolled)
        </span>
      </div>
      {error && <p className="mb-3 text-sm text-amber-600">{error}</p>}
      {events.length === 0 && (
        <p className="text-sm text-gray-500">Waiting for the next scan...</p>
      )}
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">
        {events.map((event) => (
          <div
            key={event.id}
            className="flex flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
          >
            {event.person_photo || event.captured_photo ? (
              <img
                src={event.person_photo || event.captured_photo}
                alt=""
                className="aspect-square w-full object-cover"
              />
            ) : (
              <div className="flex aspect-square w-full items-center justify-center bg-gray-200 text-[10px] text-gray-500">
                No photo
              </div>
            )}
            <div className="flex flex-1 flex-col gap-0.5 p-2">
              <p className="truncate text-xs font-medium text-gray-900">{event.person_name || "Unknown"}</p>
              <p className="truncate text-[10px] text-gray-500">
                {event.student_or_employee_id || "No ID on file"}
              </p>
              <p className="truncate text-[10px] text-gray-400">
                {new Date(event.timestamp).toLocaleTimeString()}
              </p>
              <div className="mt-1">
                <EnrollmentBadge status={event.status} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
