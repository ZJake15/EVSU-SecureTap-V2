import { NavLink } from "react-router-dom";
import logo from "../assets/logo.png";
import { useAuth } from "../auth/AuthContext";

// Mirrors the backend's permission matrix (accounts/permissions.py) - kept
// in sync by hand since there's no shared source of truth between the two
// codebases. This only controls what's *shown*; the actual enforcement is
// server-side (see ProtectedRoute for the route-level mirror of the same
// matrix, and each API view's permission_classes for the real gate).
const ALL_LINKS = [
  { to: "/", label: "Live Monitoring", roles: ["admin", "saso", "security_officer"] },
  { to: "/logs", label: "Logs", roles: ["admin", "saso", "security_officer"] },
  { to: "/users", label: "User Management", roles: ["admin", "saso"] },
  { to: "/reports", label: "Reports", roles: ["admin", "saso"] },
  { to: "/audit-log", label: "Audit Log", roles: ["admin", "saso"] },
  { to: "/accounts", label: "Accounts", roles: ["admin"] },
  { to: "/settings", label: "Settings", roles: ["admin"] },
];

const ROLE_LABELS = {
  admin: "Admin",
  saso: "SASO",
  security_officer: "Security Officer",
};

export default function Navbar() {
  const { user, logout } = useAuth();
  const links = ALL_LINKS.filter((link) => link.roles.includes(user?.role));

  return (
    <header className="bg-maroon text-white shadow-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          <img src={logo} alt="EVSU SecureTap" className="h-9 w-9" />
          <div>
            <p className="font-display text-base font-semibold leading-tight tracking-tight">
              EVSU SecureTap
            </p>
            <p className="text-xs leading-tight text-gold-200/90">Campus Entry Monitoring</p>
          </div>
        </div>
        <nav className="flex items-center gap-1">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) =>
                `rounded-t px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
                  isActive
                    ? "border-gold-400 bg-white/10 text-white"
                    : "border-transparent text-white/80 hover:bg-white/10 hover:text-white"
                }`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium uppercase tracking-wide text-gold-200/90">
            {ROLE_LABELS[user?.role] || user?.role}
            {user?.role === "security_officer" && (
              <span className="ml-1 text-gold-200/70">
                &middot; {user.gateLocation || "no gate assigned"}
              </span>
            )}
          </span>
          <button
            type="button"
            onClick={logout}
            className="rounded border border-white/25 px-3 py-1.5 text-sm font-medium transition-colors hover:bg-white/10"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
