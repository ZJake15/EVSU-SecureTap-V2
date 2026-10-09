import { Outlet } from "react-router-dom";
import Navbar from "./Navbar";

export default function Layout() {
  return (
    <div className="flex min-h-screen bg-canvas print:block print:min-h-0 print:bg-surface">
      <Navbar />
      <main className="flex min-w-0 flex-1 flex-col px-s5 pb-s5 pt-s6 xl:px-s6 print:p-0">
        <Outlet />
      </main>
    </div>
  );
}
