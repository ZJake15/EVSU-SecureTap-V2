import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import logo from "../assets/logo.png";
import { IDLE_NOTICE_KEY, useAuth } from "../auth/AuthContext";
import { Field, Icon, Notice } from "../components/ui";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  // Read once: set by "Automatic logout" when it signed this user out.
  const [idleMinutes] = useState(() => {
    const value = sessionStorage.getItem(IDLE_NOTICE_KEY);
    if (value) sessionStorage.removeItem(IDLE_NOTICE_KEY);
    return value;
  });

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);
    try {
      await login(username, password);
      const redirectTo = location.state?.from?.pathname || "/";
      navigate(redirectTo, { replace: true });
    } catch (err) {
      const status = err.response?.status;
      setError(
        status === 401
          ? "Invalid username or password."
          : status === 403 && err.response.data?.detail
            ? err.response.data.detail // e.g. locked after too many wrong passwords
            : "Unable to reach the server. Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="grid min-h-screen grid-cols-1 bg-canvas md:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
      <div className="flex flex-col justify-between gap-s6 bg-maroon-deep px-s5 py-s6 text-white md:pb-s7 md:pl-s8 md:pr-s7 md:pt-s8">
        <div className="flex flex-col gap-s5 md:gap-s6">
          <img src={logo} alt="EVSU seal" className="h-20 w-20 md:h-28 md:w-28" />
          <div className="flex flex-col gap-s3">
            <span className="font-display stretch-wide text-5xl font-black leading-[0.95] tracking-[0.16em] md:text-[72px]">
              EVSU
            </span>
            <span className="font-display stretch-semi text-5xl font-extrabold leading-[0.95] tracking-[-0.02em] md:text-[72px]">
              SecureTap
            </span>
          </div>
          <div className="h-[3px] w-[68px] bg-brass" />
          <span className="max-w-[420px] text-xl leading-snug">Campus Entry Monitoring System</span>
        </div>
        <span className="text-sm text-line">Eastern Visayas State University &middot; Tacloban City &middot; Est. 1907</span>
      </div>

      <div className="flex flex-col justify-center px-s5 py-s6 md:px-s7 md:pb-s8 md:pt-s7">
        <form onSubmit={handleSubmit} className="flex w-full max-w-[380px] flex-col gap-s5">
          <div className="flex flex-col gap-s2">
            <span className="t-eyebrow">Staff sign-in</span>
            <h1 className="t-title">Sign in</h1>
          </div>

          {error && (
            <Notice tone="danger">
              <span className="font-bold">{error}</span>
            </Notice>
          )}
          {!error && idleMinutes && (
            <Notice tone="prompt" icon="clock">
              You were signed out after {idleMinutes} minute{idleMinutes === "1" ? "" : "s"} with no activity.
            </Notice>
          )}

          <div className="flex flex-col gap-s4">
            <Field label="Username">
              <input
                id="username"
                type="text"
                required
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="input text-base"
              />
            </Field>
            <Field label="Password">
              <input
                id="password"
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="input text-base"
              />
            </Field>
          </div>

          <button type="submit" disabled={isSubmitting} className="btn-primary w-full text-base">
            {isSubmitting ? "Signing in…" : "Sign in"}
            {!isSubmitting && <Icon name="arrow-right" bold size={16} />}
          </button>
        </form>
      </div>
    </div>
  );
}
