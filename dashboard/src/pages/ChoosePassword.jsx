import { useState } from "react";
import logo from "../assets/logo.png";
import { useAuth } from "../auth/AuthContext";
import { Field, Icon, Notice } from "../components/ui";

function FieldError({ text }) {
  return text ? <span className="text-sm font-bold text-danger">{text}</span> : null;
}

// Shown instead of the whole dashboard while the signed-in account's password
// was set by an Admin (a new account, or "forgot my password" reset on the
// Accounts page) - the server answers nothing else until it's changed.
export default function ChoosePassword() {
  const { user, logout, changePassword } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [errors, setErrors] = useState({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (next !== again) {
      setErrors({ again: "The two new passwords don't match." });
      return;
    }
    setErrors({});
    setIsSubmitting(true);
    try {
      await changePassword(current, next);
    } catch (err) {
      const data = err.response?.data || {};
      setErrors({
        current: data.current_password?.[0],
        next: data.new_password?.join(" "),
        form: data.detail || (!err.response ? "Unable to reach the server. Please try again." : undefined),
      });
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
            <span className="t-eyebrow">Signed in as {user?.username}</span>
            <h1 className="t-title">Choose a new password</h1>
            <p className="text-sm text-ink-600">
              An Admin set your current password. Choose your own to continue - only you will know it.
            </p>
          </div>

          {errors.form && (
            <Notice tone="danger">
              <span className="font-bold">{errors.form}</span>
            </Notice>
          )}

          <div className="flex flex-col gap-s4">
            <Field label="Current password" hint="The one the Admin gave you.">
              <input
                type="password"
                required
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
                className="input text-base"
              />
              <FieldError text={errors.current} />
            </Field>
            <Field label="New password" hint="At least 10 characters, not a common password.">
              <input
                type="password"
                required
                autoComplete="new-password"
                value={next}
                onChange={(event) => setNext(event.target.value)}
                className="input text-base"
              />
              <FieldError text={errors.next} />
            </Field>
            <Field label="New password again">
              <input
                type="password"
                required
                autoComplete="new-password"
                value={again}
                onChange={(event) => setAgain(event.target.value)}
                className="input text-base"
              />
              <FieldError text={errors.again} />
            </Field>
          </div>

          <button type="submit" disabled={isSubmitting} className="btn-primary w-full text-base">
            {isSubmitting ? "Saving…" : "Save and continue"}
            {!isSubmitting && <Icon name="arrow-right" bold size={16} />}
          </button>
          <button type="button" onClick={logout} className="btn-ghost w-full">
            Sign out
          </button>
        </form>
      </div>
    </div>
  );
}
