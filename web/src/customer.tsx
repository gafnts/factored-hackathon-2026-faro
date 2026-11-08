import { useCallback, useEffect, useState } from "react";

import {
  configureAuth,
  currentSignIn,
  enterAsGuest,
  type SignedIn,
  signInWith,
  signOutHere,
} from "./auth";
import { Chat } from "./chat/chat";
import { type Config, loadConfig } from "./config";
import type { Language } from "./contracts/chat";
import { faces } from "./faces";
import { LogOut, SquarePen } from "./icons";
import { SignIn, Terms } from "./pages/sign-in";
import { drawRuntimeSession, runtimeSession } from "./session";
import { RailButton, Shell } from "./shell";
import { TEXTS } from "./texts";

type State =
  | { kind: "loading" }
  | { kind: "broken" }
  | { kind: "signed-out"; config: Config; ended: boolean }
  | { kind: "signed-in"; config: Config; signedIn: SignedIn; session: string };

// The customer's route: sign-in, then the chat, until the sign-in's hour ends (POL-09). The page's language starts
// from the browser's and follows the bar's switch (ADR-0007, Routes); the agent's replies follow POL-50 on their own.
export function Customer({ language: starting }: { language: Language }) {
  const [language, setLanguage] = useState(starting);
  const texts = TEXTS[language];
  const [state, setState] = useState<State>({ kind: "loading" });
  // Each new conversation mounts the chat again, which draws a thread ID of its own on the same runtime session
  // (ADR-0007's amendment of 2026-09-30); the earlier one is out of reach once left.
  const [conversation, setConversation] = useState(0);
  const [running, setRunning] = useState(false);

  const end = useCallback(async (config: Config, ended: boolean) => {
    await signOutHere().catch(() => undefined);
    setState({ kind: "signed-out", config, ended });
  }, []);

  useEffect(() => {
    const unmounted = new AbortController();
    void (async () => {
      let config: Config;
      try {
        [config] = await Promise.all([loadConfig(), faces()]);
      } catch {
        if (!unmounted.signal.aborted) setState({ kind: "broken" });
        return;
      }
      configureAuth(config, "customers");
      const signedIn = await currentSignIn();
      if (unmounted.signal.aborted) return;
      if (!signedIn) setState({ kind: "signed-out", config, ended: false });
      else if (Date.now() >= signedIn.endsAt) await end(config, true);
      else
        setState({
          kind: "signed-in",
          config,
          signedIn,
          session: runtimeSession(),
        });
    })();
    return () => {
      unmounted.abort();
    };
  }, [end]);

  const endsAt = state.kind === "signed-in" ? state.signedIn.endsAt : null;
  const config = state.kind === "signed-in" ? state.config : null;
  useEffect(() => {
    if (endsAt === null || config === null) return;
    const timer = window.setTimeout(
      () => void end(config, true),
      endsAt - Date.now(),
    );
    return () => {
      window.clearTimeout(timer);
    };
  }, [endsAt, config, end]);

  const signIn = useCallback(
    async (username: string, password: string) => {
      if (state.kind !== "signed-out") return "refused" as const;
      const outcome = await signInWith(username, password);
      const signedIn = outcome === "signed_in" ? await currentSignIn() : null;
      if (outcome === "signed_in" && !signedIn) return "refused" as const;
      if (signedIn) {
        setState({
          kind: "signed-in",
          config: state.config,
          signedIn,
          session: drawRuntimeSession(),
        });
      }
      return outcome;
    },
    [state],
  );

  const guest = useCallback(async () => {
    if (state.kind !== "signed-out") return "unreachable" as const;
    const outcome = await enterAsGuest();
    const signedIn = outcome === "signed_in" ? await currentSignIn() : null;
    if (outcome === "signed_in" && !signedIn) return "unreachable" as const;
    if (signedIn) {
      setState({
        kind: "signed-in",
        config: state.config,
        signedIn,
        session: drawRuntimeSession(),
      });
    }
    return outcome;
  }, [state]);

  const ended = useCallback(() => {
    if (config) void end(config, true);
  }, [config, end]);

  const newConversation = useCallback(() => {
    setRunning(false);
    setConversation((count) => count + 1);
  }, []);

  // The bare ground until the page can arrive whole, its faces in, rather than a frame first and the rest after.
  if (state.kind === "loading") return <div className="h-dvh" />;
  if (state.kind === "broken") {
    return (
      <Shell language={language} onLanguage={setLanguage}>
        <p
          role="alert"
          className="mx-auto w-full max-w-3xl px-4 py-10 text-lamp sm:px-6"
        >
          {texts.broken}
        </p>
      </Shell>
    );
  }
  if (state.kind === "signed-out") {
    return (
      <Shell
        language={language}
        signIn
        onLanguage={setLanguage}
        footer={<Terms language={language} />}
      >
        <SignIn
          language={language}
          ended={state.ended}
          onSignIn={signIn}
          onGuest={guest}
        />
      </Shell>
    );
  }

  const time = new Intl.DateTimeFormat(language, { timeStyle: "short" }).format(
    state.signedIn.endsAt,
  );
  return (
    <Shell
      language={language}
      note={texts.signIn.endsAt(time)}
      running={running}
      onNew={newConversation}
      onLanguage={setLanguage}
      rail={(expanded) => (
        <>
          <RailButton
            icon={<SquarePen />}
            label={texts.rail.newChat}
            expanded={expanded}
            disabled={running}
            onClick={newConversation}
          />
          <RailButton
            icon={<LogOut />}
            label={texts.signIn.signOut}
            expanded={expanded}
            className="sm:mt-auto"
            onClick={() => void end(state.config, false)}
          />
        </>
      )}
    >
      <Chat
        key={conversation}
        url={state.config.runtime_url}
        session={state.session}
        language={language}
        onSignInEnded={ended}
        onRunning={setRunning}
      />
    </Shell>
  );
}
