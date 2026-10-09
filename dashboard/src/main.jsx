import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "@phosphor-icons/web/regular";
import "@phosphor-icons/web/bold";
import "./fonts.css";
import "./index.css";
import App from "./App.jsx";
import { AuthProvider } from "./auth/AuthContext.jsx";

// React Router's "data router" setup. It's what makes useBlocker work - the
// Settings page uses it to warn before leaving with unsaved changes. App
// still declares its own <Routes>, under this one catch-all route, so the
// pages themselves didn't change.
const router = createBrowserRouter([
  {
    path: "*",
    element: (
      <AuthProvider>
        <App />
      </AuthProvider>
    ),
  },
]);

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
