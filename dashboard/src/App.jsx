import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import { useAuth } from "./auth/AuthContext";
import Login from "./pages/Login";
import LiveMonitoring from "./pages/LiveMonitoring";
import Logs from "./pages/Logs";
import Users from "./pages/Users";
import Reports from "./pages/Reports";

export default function App() {
  const { user } = useAuth();

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<LiveMonitoring />} />
        <Route path="logs" element={<Logs />} />
        <Route
          path="users"
          element={
            <ProtectedRoute roles={["admin", "it"]}>
              <Users />
            </ProtectedRoute>
          }
        />
        <Route
          path="reports"
          element={
            <ProtectedRoute roles={["admin", "it"]}>
              <Reports />
            </ProtectedRoute>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
