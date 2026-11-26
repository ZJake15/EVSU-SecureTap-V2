import { Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import Navbar from "./Navbar";
import OfficeAlarms from "./OfficeAlarms";

export default function Layout() {
  const { user } = useAuth();
  // The office alarm pops up on every page - for the security office (Admin
  // and SASO); a Security Officer gets their own gate's alarms on the gate monitor.
  const officeAlarms = user?.role === "admin" || user?.role === "saso";
  return (
    <div className="flex min-h-screen bg-canvas print:block print:min-h-0 print:bg-surface">
      <Navbar />
      <main className="flex min-w-0 flex-1 flex-col px-s5 pb-s5 pt-s6 xl:px-s6 print:p-0">
        <Outlet />
      </main>
      {officeAlarms && <OfficeAlarms />}
    </div>
  );
}
