import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { jwtDecode } from "jwt-decode";
import apiClient, { tokenStore } from "../api/client";

const AuthContext = createContext(null);

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

  useEffect(() => {
    const handleForcedLogout = () => setUser(null);
    window.addEventListener("securetap:logout", handleForcedLogout);
    return () => window.removeEventListener("securetap:logout", handleForcedLogout);
  }, []);

  const value = useMemo(() => ({ user, login, logout }), [user, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
