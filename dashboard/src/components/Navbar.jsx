import { NavLink } from "react-router-dom";
import logo from "../assets/logo.svg";
import { useAuth } from "../auth/AuthContext";

const ALL_LINKS = [
  { to: "/", label: "Live Monitoring", roles: ["admin", "security", "it"] },
  { to: "/logs", label: "Logs", roles: ["admin", "security", "it"] },
  { to: "/users", label: "User Management", roles: ["admin", "it"] },
  { to: "/reports", label: "Reports", roles: ["admin", "it"] },
];

export default function Navbar() {
  const { user, logout } = useAuth();
  const links = ALL_LINKS.filter((link) => link.roles.includes(user?.role));

  return (
    <header className="bg-maroon text-white shadow">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          <img src={logo} alt="EVSU SecureTap" className="h-9 w-9" />
          <div>
            <p className="text-sm font-semibold leading-tight">EVSU SecureTap</p>
            <p className="text-xs leading-tight text-white/70">Campus Entry Monitoring</p>
          </div>
        </div>
        <nav className="flex items-center gap-1">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) =>
                `rounded px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? "bg-white/15" : "hover:bg-white/10"
                }`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <span className="text-xs uppercase tracking-wide text-white/70">{user?.role}</span>
          <button
            type="button"
            onClick={logout}
            className="rounded border border-white/30 px-3 py-1.5 text-sm font-medium hover:bg-white/10"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
