import { useCallback, useEffect, useState } from "react";
import apiClient from "../api/client";

// EVSU's schools, offices and departments (backend users/org_views.py),
// plus the staff types and year levels - for the registration form, the
// filters, and Settings > Schools, Offices & Departments.

export const EMPTY_ORG = { units: [], staff_types: [], year_levels: [] };

export function useOrgLists() {
  const [org, setOrg] = useState(EMPTY_ORG);
  const [error, setError] = useState("");
  const reload = useCallback(() => {
    apiClient
      .get("/org-units")
      .then(({ data }) => {
        setOrg(data);
        setError("");
      })
      .catch(() => setError("Couldn't load the list of schools and offices."));
  }, []);
  useEffect(reload, [reload]);
  return { org, setOrg, error, reload };
}

// Units grouped for an <optgroup> list: schools first, then each office group.
export function groupedUnits(units, { kind = null, keepId = null } = {}) {
  const groups = new Map();
  units
    .filter((unit) => (unit.is_active || String(unit.id) === String(keepId)) && (!kind || unit.kind === kind))
    .forEach((unit) => {
      const label = unit.kind === "school" ? "Schools" : `Offices · ${unit.group || "Other"}`;
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(unit);
    });
  return [...groups.entries()];
}
