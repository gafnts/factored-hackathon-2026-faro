import {
  type KeyboardEvent,
  type ReactNode,
  type SubmitEvent,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import type { GuestOutcome, SignInOutcome } from "../auth";
import type { Language } from "../contracts/chat";
import { Grid } from "../grid";
import { dayEnds, TEXTS } from "../texts";

// Whole cells either side of the grid's center, so the card's edges fall on the lines its cleared block hides.
function Card({ children }: { children: ReactNode }) {
  return (
    <section className="ml-[max(0px,calc(50%-var(--span)*var(--cell)))] w-[calc(2*var(--span)*var(--cell)+1px)] max-w-full border border-white/10 glass [--span:5] sm:[--span:4]">
      {children}
    </section>
  );
}

function Alert({ children }: { children: ReactNode }) {
  // Small enough that the session's-end sentence holds one line in both languages.
  return (
    <p role="alert" className="text-xs text-lamp">
      {children}
    </p>
  );
}

type Way = "guest" | "account";

const OTHER: Record<Way, Way> = { guest: "account", account: "guest" };

// The two ways in as tabs on the card's top edge, split on the grid's center line, so each spans whole cells, their
// labels on the panel's left edge. The chosen one opens into the card under a line of sea, which slides to it; the
// other sits behind a hairline. A click moves focus into the panel, as the switch it replaced did; the arrow keys move
// between the tabs.
function Tabs({
  language,
  way,
  panel,
  onWay,
}: {
  language: Language;
  way: Way;
  panel: string;
  onWay: (way: Way, focus: boolean) => void;
}) {
  const texts = TEXTS[language].signIn.tabs;
  const tabs = useRef<Record<Way, HTMLButtonElement | null>>({
    guest: null,
    account: null,
  });
  const ways: readonly Way[] = ["guest", "account"];

  function key(event: KeyboardEvent<HTMLButtonElement>) {
    const next: Way | undefined = {
      ArrowLeft: OTHER[way],
      ArrowRight: OTHER[way],
      Home: "guest" as const,
      End: "account" as const,
    }[event.key];
    if (!next) return;
    event.preventDefault();
    onWay(next, false);
    tabs.current[next]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={texts.label}
      className="relative grid grid-cols-[calc(var(--span)*var(--cell)-1px)_1fr]"
    >
      <span
        aria-hidden="true"
        className={`pointer-events-none absolute top-0 left-0 h-0.5 w-[calc(var(--span)*var(--cell)-1px)] bg-sea transition-transform duration-300 ease-out motion-reduce:transition-none ${way === "account" ? "translate-x-[calc(var(--span)*var(--cell))]" : ""}`}
      />
      {ways.map((option) => {
        const chosen = option === way;
        return (
          <button
            key={option}
            ref={(element) => {
              tabs.current[option] = element;
            }}
            type="button"
            role="tab"
            id={`${panel}-${option}`}
            aria-selected={chosen}
            aria-controls={panel}
            tabIndex={chosen ? 0 : -1}
            onKeyDown={key}
            onClick={() => {
              if (!chosen) onWay(option, true);
            }}
            className={`h-11 border-b border-white/10 px-6 text-left text-sm font-medium transition-colors duration-300 sm:px-8 ${option === "account" ? "border-l" : ""} ${chosen ? "border-b-transparent text-bone" : "bg-night/40 text-bone-muted hover:text-bone"}`}
          >
            {texts[option]}
          </button>
        );
      })}
    </div>
  );
}

// The card eases to its panel's height, so the page re-centers gradually rather than jumping when the way in changes.
function Eased({ children }: { children: ReactNode }) {
  const inner = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | null>(null);

  useLayoutEffect(() => {
    const element = inner.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      setHeight(element.offsetHeight);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, []);

  return (
    <div
      style={height === null ? undefined : { height }}
      className="overflow-hidden transition-[height] duration-300 ease-out motion-reduce:transition-none"
    >
      <div ref={inner}>{children}</div>
    </div>
  );
}

// The form's title shows on the console's sign-in, where the form is alone; under the tabs it names the panel for
// screen readers only.
function Form({
  language,
  ended,
  focus,
  titled,
  onSignIn,
}: {
  language: Language;
  ended: boolean;
  focus: boolean;
  titled: boolean;
  onSignIn: (username: string, password: string) => Promise<SignInOutcome>;
}) {
  const texts = TEXTS[language].signIn;
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<SignInOutcome | null>(null);
  const username = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (focus) username.current?.focus();
  }, [focus]);

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

  return (
    <>
      <h1
        className={
          titled ? "text-2xl font-semibold tracking-[-0.025em]" : "sr-only"
        }
      >
        {texts.title}
      </h1>
      <form
        method="post"
        className={`flex flex-col gap-4 ${titled ? "mt-6" : ""}`}
        onSubmit={(event) => void submit(event)}
      >
        <label className="flex flex-col gap-1">
          <span className="text-sm font-medium">{texts.username}</span>
          <input
            ref={username}
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
        {message && <Alert>{message}</Alert>}
        <button
          type="submit"
          disabled={pending}
          className="h-11 bg-sea font-medium text-night disabled:opacity-40"
        >
          {pending ? texts.submitting : texts.submit}
        </button>
      </form>
    </>
  );
}

// Nearly everyone who arrives is a stranger, so the guest entry is the first tab. A refusal holds the button until the
// page loads again, since the day's guests and the pause don't lift in minutes.
function Guest({
  language,
  ended,
  focus,
  onGuest,
}: {
  language: Language;
  ended: boolean;
  focus: boolean;
  onGuest: () => Promise<GuestOutcome>;
}) {
  const texts = TEXTS[language].signIn;
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<GuestOutcome | null>(null);
  const enter = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (focus) enter.current?.focus();
  }, [focus]);

  async function click() {
    setPending(true);
    const result = await onGuest();
    if (result !== "signed_in") {
      setOutcome(result);
      setPending(false);
    }
  }

  const message =
    outcome === "full"
      ? texts.guest.full(dayEnds(language))
      : outcome === "paused"
        ? texts.guest.paused
        : outcome === "unreachable"
          ? texts.unreachable
          : ended
            ? texts.ended
            : null;
  const closed = outcome === "full" || outcome === "paused";

  return (
    <>
      <h1 className="sr-only">{texts.guest.title}</h1>
      <p className="text-pretty">{texts.guest.lead}</p>
      <div className="mt-6 flex flex-col gap-4">
        {message && <Alert>{message}</Alert>}
        <button
          ref={enter}
          type="button"
          disabled={pending || closed}
          onClick={() => void click()}
          className="h-11 bg-sea font-medium text-night disabled:opacity-40"
        >
          {pending ? texts.guest.entering : texts.guest.enter}
        </button>
      </div>
    </>
  );
}

// What we keep of a visit, a click before anyone enters (ADR-0007, Sign-in): the customers' sign-in's footer, in the
// notice's place, a glass bar like the one at the top, says that entering means accepting it, and its last words open
// the full list. Only the team signs in to the console, which keeps the notice.
export function Terms({ language }: { language: Language }) {
  const texts = TEXTS[language].signIn.terms;
  const dialog = useRef<HTMLDialogElement>(null);

  // The dialog leaves as its veil clears, so it closes once those animations end.
  function close() {
    const element = dialog.current;
    if (!element || element.dataset.closing !== undefined) return;
    element.dataset.closing = "";
    void Promise.allSettled(
      element.getAnimations().map((animation) => animation.finished),
    ).then(() => {
      element.close();
      delete element.dataset.closing;
    });
  }

  return (
    <footer className="animate-fade border-t border-white/10 glass-thin px-4 pt-2.5 pb-3.5 text-xs text-bone sm:px-6 sm:text-sm">
      {/* On a phone, each sentence holds a line of its own. */}
      <p className="text-pretty sm:text-right">
        <span className="max-sm:block">{texts.synthetic}</span> {texts.accept}{" "}
        <button
          type="button"
          onClick={() => {
            // Focus the dialog itself, not its first button, so nothing looks pressed and a reader starts at the title.
            dialog.current?.showModal();
            dialog.current?.focus();
          }}
          // A phone's tap finds a 44 px target, its padding taken back by the margin so the line doesn't move.
          className="underline underline-offset-4 hover:text-sea max-sm:-my-3.5 max-sm:py-3.5"
        >
          {texts.more}
        </button>
        .
      </p>
      {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions -- a click on the backdrop reaches the dialog itself, outside its box: the mouse's way out, where Escape and the button are the keyboard's */}
      <dialog
        ref={dialog}
        tabIndex={-1}
        aria-labelledby="terms"
        onCancel={(event) => {
          event.preventDefault();
          close();
        }}
        onClick={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const outside =
            event.clientX < box.left ||
            event.clientX > box.right ||
            event.clientY < box.top ||
            event.clientY > box.bottom;
          if (event.target === event.currentTarget && outside) close();
        }}
        className="m-auto w-[calc(2*var(--span)*var(--cell)+1px)] max-w-[calc(100%-2rem)] animate-message border border-white/10 glass p-6 text-bone [--span:5] backdrop:animate-veil backdrop:bg-night/70 backdrop:backdrop-blur-md focus-visible:outline-none data-closing:animate-leave data-closing:backdrop:animate-unveil motion-reduce:animate-fade sm:p-8 sm:[--span:4]"
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
          onClick={close}
          className="mt-8 h-11 w-full border border-white/10 font-medium hover:bg-white/5"
        >
          {texts.close}
        </button>
      </dialog>
    </footer>
  );
}

// The customer's sign-in and the console's alike, inside the shell: the banner's grid, with the wordmark over the card
// in its cleared block (the identity guide's Surfaces). The customers' opens on the guest entry, with the form as the
// second tab; the console, which only the team signs in to, has the form alone.
export function SignIn({
  language,
  ended,
  onSignIn,
  onGuest,
}: {
  language: Language;
  ended: boolean;
  onSignIn: (username: string, password: string) => Promise<SignInOutcome>;
  onGuest?: () => Promise<GuestOutcome>;
}) {
  const panel = useId();
  // Focus moves with a click on a tab, never on the page's arrival; a panel fades in only when the way in changes.
  const [mode, setMode] = useState<{
    way: Way;
    focus: boolean;
    moved: boolean;
  }>({ way: onGuest ? "guest" : "account", focus: false, moved: false });
  return (
    <Grid layout="sign-in">
      <div className="flex flex-1 flex-col justify-center px-4 py-6">
        <p className="mx-auto w-full max-w-sm pb-6 text-center font-display text-6xl font-semibold tracking-[-0.07em]">
          Faro
        </p>
        <Card>
          {onGuest ? (
            <>
              <Tabs
                language={language}
                way={mode.way}
                panel={panel}
                onWay={(way, focus) => {
                  setMode({ way, focus, moved: true });
                }}
              />
              <Eased>
                <div
                  key={mode.way}
                  role="tabpanel"
                  id={panel}
                  aria-labelledby={`${panel}-${mode.way}`}
                  className={`p-6 sm:p-8 ${mode.moved ? "animate-fade" : ""}`}
                >
                  {mode.way === "guest" ? (
                    <Guest
                      language={language}
                      ended={ended}
                      focus={mode.focus}
                      onGuest={onGuest}
                    />
                  ) : (
                    <Form
                      language={language}
                      ended={ended}
                      focus={mode.focus}
                      titled={false}
                      onSignIn={onSignIn}
                    />
                  )}
                </div>
              </Eased>
            </>
          ) : (
            <div className="p-6 sm:p-8">
              <Form
                language={language}
                ended={ended}
                focus={false}
                titled
                onSignIn={onSignIn}
              />
            </div>
          )}
        </Card>
      </div>
    </Grid>
  );
}
