import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { jwtDecode } from "jwt-decode";
import apiClient, { tokenStore } from "../api/client";

const AuthContext = createContext(null);

// Set when "Automatic logout" signs someone out - the Login page reads it
// (once) to explain why they're back there.
export const IDLE_NOTICE_KEY = "securetap_idle_logout";
const ACTIVITY_EVENTS = ["mousedown", "mousemove", "keydown", "scroll", "touchstart"];
const POLICY_REFRESH_MS = 5 * 60 * 1000;

function decodeUser(accessToken) {
  if (!accessToken) return null;
  try {
    const claims = jwtDecode(accessToken);
    if (claims.exp * 1000 < Date.now()) return null;
    return {
      username: claims.username,
      role: claims.role,
      fullName: claims.full_name,
      // Only meaningful for a security_officer - null for every other role,
      // and null for an unconfigured officer account too (see accounts.
      // permissions.get_assigned_gate on the backend - the same "fail
      // closed, not open" rule applies here: no gate claim means the pages
      // that scope by it should show nothing, not everything).
      gateLocation: claims.gate_location ?? null,
      // Their password was set by an Admin: only "Choose a new password"
      // until they do (the server refuses everything else anyway).
      mustChangePassword: Boolean(claims.must_change_password),
    };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => decodeUser(tokenStore.getAccess()));

  const login = useCallback(async (username, password) => {
    const { data } = await apiClient.post("/auth/login", { username, password });
    tokenStore.setTokens(data.access, data.refresh);
    const nextUser = decodeUser(data.access);
    setUser(nextUser);
    return nextUser;
  }, []);

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  // Choosing their own password: fresh tokens come back, without the flag.
  const changePassword = useCallback(async (currentPassword, newPassword) => {
    const { data } = await apiClient.post("/auth/change-password", {
      current_password: currentPassword,
      new_password: newPassword,
    });
    tokenStore.setTokens(data.access, data.refresh);
    setUser(decodeUser(data.access));
  }, []);

  useEffect(() => {
    const handleForcedLogout = () => setUser(null);
    // The server says this account must choose a new password first (an
    // Admin reset it while they were signed in, say).
    const handlePasswordChange = () => setUser((prev) => (prev ? { ...prev, mustChangePassword: true } : prev));
    window.addEventListener("securetap:logout", handleForcedLogout);
    window.addEventListener("securetap:password-change", handlePasswordChange);
    return () => {
      window.removeEventListener("securetap:logout", handleForcedLogout);
      window.removeEventListener("securetap:password-change", handlePasswordChange);
    };
  }, []);

  // The few Settings-page values every signed-in page needs (inactivity
  // logout, single-photo registration). Re-read every few minutes, so an
  // Admin's change reaches people who are already signed in.
  const [policy, setPolicy] = useState(null);
  useEffect(() => {
    if (!user || user.mustChangePassword) {
      setPolicy(null);
      return undefined;
    }
    let cancelled = false;
    const load = () =>
      apiClient
        .get("/session-policy")
        .then(({ data }) => !cancelled && setPolicy(data))
        .catch(() => {});
    load();
    const timer = setInterval(load, POLICY_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [user]);

  // Settings page: "Automatic logout". Any mouse, key, scroll or touch
  // activity restarts the countdown; when it runs out the user is signed
  // out and the Login page says why.
  const idleMinutes = policy?.idle_logout_minutes || 0;
  useEffect(() => {
    if (!user || !idleMinutes) return undefined;
    let timer;
    const signOut = () => {
      sessionStorage.setItem(IDLE_NOTICE_KEY, String(idleMinutes));
      tokenStore.clear();
      setUser(null);
    };
    const restart = () => {
      clearTimeout(timer);
      timer = setTimeout(signOut, idleMinutes * 60 * 1000);
    };
    ACTIVITY_EVENTS.forEach((name) => window.addEventListener(name, restart, { passive: true }));
    restart();
    return () => {
      clearTimeout(timer);
      ACTIVITY_EVENTS.forEach((name) => window.removeEventListener(name, restart));
    };
  }, [user, idleMinutes]);

  const value = useMemo(
    () => ({ user, login, logout, policy, changePassword }),
    [user, login, logout, policy, changePassword]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
