import { type SubmitEvent, useRef, useState } from "react";

import type { SignInOutcome } from "../auth";
import type { Language } from "../contracts/chat";
import { Grid } from "../grid";
import { TEXTS } from "../texts";

function Form({
  language,
  ended,
  onSignIn,
}: {
  language: Language;
  ended: boolean;
  onSignIn: (username: string, password: string) => Promise<SignInOutcome>;
}) {
  const texts = TEXTS[language].signIn;
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<SignInOutcome | null>(null);

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    const field = (name: string) => {
      const value = form.get(name);
      return typeof value === "string" ? value : "";
    };
    const result = await onSignIn(field("username").trim(), field("password"));
    if (result !== "signed_in") {
      setOutcome(result);
      setPending(false);
    }
  }

  const message =
    outcome === "refused"
      ? texts.refused
      : outcome === "unreachable"
        ? texts.unreachable
        : ended
          ? texts.ended
          : null;

  // Whole cells either side of the grid's center, so the form's edges fall on the lines its cleared block hides.
  return (
    <section className="ml-[max(0px,calc(50%-var(--span)*var(--cell)))] w-[calc(2*var(--span)*var(--cell)+1px)] max-w-full border border-white/10 glass px-8 py-8 [--span:5] sm:[--span:4]">
      <h1 className="text-2xl font-semibold tracking-[-0.025em]">
        {texts.title}
      </h1>
      <form
        method="post"
        className="mt-6 flex flex-col gap-4"
        onSubmit={(event) => void submit(event)}
      >
        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">{texts.username}</span>
          <input
            name="username"
            autoComplete="username"
            required
            className="border border-white/10 bg-night px-3 py-2.5 text-base"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">{texts.password}</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            className="border border-white/10 bg-night px-3 py-2.5 text-base"
          />
        </label>
        {message && (
          // Small enough that the session's-end sentence holds one line in both languages.
          <p role="alert" className="text-xs text-lamp">
            {message}
          </p>
        )}
        <button
          type="submit"
          disabled={pending}
          className="h-11 bg-sea font-medium text-night disabled:opacity-40"
        >
          {pending ? texts.submitting : texts.submit}
        </button>
      </form>
    </section>
  );
}

// What we keep of a visit, said before anyone enters (ADR-0007, Sign-in): the customers' sign-in's footer, in the
// notice's place, a glass bar like the one at the top. Entering means accepting it, so its one button opens the full
// list. Only the team signs in to the console, which keeps the notice.
export function Terms({ language }: { language: Language }) {
  const texts = TEXTS[language].signIn.terms;
  const dialog = useRef<HTMLDialogElement>(null);
  return (
    <footer className="animate-fade border-t border-white/10 glass-thin px-4 py-3 text-xs text-bone sm:px-6 sm:text-sm">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-8">
        <p className="text-pretty">{texts.banner}</p>
        <button
          type="button"
          onClick={() => dialog.current?.showModal()}
          className="shrink-0 self-start underline underline-offset-4 hover:text-sea sm:self-auto"
        >
          {texts.more}
        </button>
      </div>
      <dialog
        ref={dialog}
        aria-labelledby="terms"
        className="m-auto w-[calc(2*var(--span)*var(--cell)+1px)] max-w-[calc(100%-2rem)] border border-white/10 glass px-8 py-8 text-bone [--span:5] backdrop:bg-night/70 sm:[--span:4]"
      >
        <h2 id="terms" className="text-2xl font-semibold tracking-tight">
          {texts.title}
        </h2>
        <ul className="mt-6 flex flex-col gap-3 text-sm text-bone-muted">
          {texts.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => dialog.current?.close()}
          className="mt-8 h-11 w-full border border-white/10 font-medium hover:bg-white/5"
        >
          {texts.close}
        </button>
      </dialog>
    </footer>
  );
}

// The customer's sign-in and the console's alike, inside the shell: the banner's grid, with the wordmark over the form
// in its cleared block (the identity guide's Surfaces).
export function SignIn({
  language,
  ended,
  onSignIn,
}: {
  language: Language;
  ended: boolean;
  onSignIn: (username: string, password: string) => Promise<SignInOutcome>;
}) {
  return (
    <Grid layout="sign-in">
      <div className="flex flex-1 flex-col justify-center px-4 py-6">
        <p className="mx-auto w-full max-w-sm pb-6 text-center font-display text-6xl font-semibold tracking-[-0.07em]">
          Faro
        </p>
        <Form language={language} ended={ended} onSignIn={onSignIn} />
      </div>
    </Grid>
  );
}
