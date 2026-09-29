import { NavLink } from "react-router-dom";
import logo from "../assets/logo.png";
import { useAuth } from "../auth/AuthContext";
import { Icon } from "./ui";

// Mirrors the backend's permission matrix (accounts/permissions.py) - kept
// in sync by hand since there's no shared source of truth between the two
// codebases. This only controls what's *shown*; the actual enforcement is
// server-side (see ProtectedRoute for the route-level mirror of the same
// matrix, and each API view's permission_classes for the real gate).
const ALL_LINKS = [
  { to: "/", label: "Live Monitoring", icon: "broadcast", roles: ["admin", "saso", "security_officer"] },
  { to: "/logs", label: "Logs", icon: "list-bullets", roles: ["admin", "saso", "security_officer"] },
  { to: "/users", label: "User Management", icon: "users", roles: ["admin", "saso"] },
  { to: "/reports", label: "Reports", icon: "chart-bar", roles: ["admin", "saso"] },
  { to: "/audit-log", label: "Audit Log", icon: "clipboard-text", roles: ["admin", "saso"] },
  { to: "/accounts", label: "Accounts", icon: "user-gear", roles: ["admin"] },
  { to: "/settings", label: "Settings", icon: "gear-six", roles: ["admin"] },
];

const ROLE_LABELS = {
  admin: "Admin",
  saso: "Security Manager (SASO)",
  security_officer: "Security Officer",
};

// The left rail: seal + wordmark, one link per page this role can open (the
// active one gets a brass bar and maroon fill), and the signed-in account
// with Log out pinned to the bottom.
export default function Navbar() {
  const { user, logout } = useAuth();
  const links = ALL_LINKS.filter((link) => link.roles.includes(user?.role));
  const roleLabel = ROLE_LABELS[user?.role] || user?.role;

  return (
    <aside className="sticky top-0 flex h-screen w-[72px] flex-none flex-col bg-maroon-deep pb-s4 pt-s5 text-white lg:w-[232px]">
      <div className="flex items-center gap-s3 px-4 pb-s6 lg:px-5">
        <img src={logo} alt="EVSU seal" className="h-10 w-10 flex-none" />
        <div className="hidden flex-col gap-0.5 lg:flex">
          <span className="font-display stretch-wide text-xl font-black leading-none tracking-[0.14em]">EVSU</span>
          <span className="font-display stretch-semi text-xl font-bold leading-none">SecureTap</span>
        </div>
      </div>

      <nav className="flex flex-col gap-s1">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.to === "/"}
            title={link.label}
            className={({ isActive }) =>
              `flex h-10 items-center gap-s3 border-l-[3px] pl-[23px] pr-5 text-sm transition-colors lg:pl-[17px] ${
                isActive
                  ? "border-brass bg-maroon font-bold text-white hover:text-white"
                  : "border-transparent text-line hover:bg-maroon/60 hover:text-white"
              }`
            }
          >
            <Icon name={link.icon} size={20} />
            <span className="hidden lg:inline">{link.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="flex-1" />

      <div className="flex flex-col gap-s3 border-t border-maroon px-3 pt-s4 lg:px-5">
        <div className="hidden flex-col gap-s1 lg:flex">
          <span className="truncate text-sm font-bold">{user?.fullName || user?.username}</span>
          <span className="text-xs text-line">
            {roleLabel}
            {user?.role === "security_officer" && <> &middot; {user.gateLocation || "no gate assigned"}</>}
          </span>
        </div>
        <button
          type="button"
          onClick={logout}
          title="Log out"
          className="flex h-10 items-center justify-center gap-s2 rounded-md border border-line text-sm font-bold text-white transition-colors hover:bg-maroon"
        >
          <Icon name="sign-out" size={16} />
          <span className="hidden lg:inline">Log out</span>
        </button>
      </div>
    </aside>
  );
}
