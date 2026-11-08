import {
  AssistantRuntimeProvider,
  AuiIf,
  ComposerPrimitive,
  MessagePrimitive,
  type TextMessagePartProps,
  ThreadPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  useAgUiInterrupts,
  useAgUiRuntime,
  useAgUiSteerAway,
} from "@assistant-ui/react-ag-ui";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { Language } from "../contracts/chat";
import { Grid } from "../grid";
import { ArrowUp } from "../icons";
import {
  createAgent,
  type Fetch,
  problemOf,
  runtimeFetch,
  warmUp,
} from "../runtime";
import { type Problem, problemText, TEXTS } from "../texts";
import { Controls, type Shown, ShownControls } from "./control";
import { Reply } from "./reply";

// The contract's limit on a customer's message.
const MAX_MESSAGE = 2000;

function UserText({ text }: TextMessagePartProps) {
  return <p className="whitespace-pre-wrap">{text}</p>;
}

function ReplyText({ text }: TextMessagePartProps) {
  return <Reply text={text} />;
}

function UserMessage() {
  return (
    <MessagePrimitive.Root
      data-author="customer"
      className="ml-auto max-w-[75%] animate-message bg-night-bubble px-5 py-3 wrap-break-word motion-reduce:animate-fade"
    >
      <MessagePrimitive.Parts components={{ Text: UserText }} />
    </MessagePrimitive.Root>
  );
}

// Faro's replies sit on the ground, full width and without a bubble, so a long one reads as text (the identity
// guide's Motifs: never on glass).
function AssistantMessage() {
  return (
    <MessagePrimitive.Root
      data-author="faro"
      className="animate-message wrap-break-word empty:hidden motion-reduce:animate-fade"
    >
      <MessagePrimitive.Parts components={{ Text: ReplyText }} />
      <Controls />
    </MessagePrimitive.Root>
  );
}

function Working({ label }: { label: string }) {
  return (
    <div
      role="status"
      className="relative h-1 w-32 animate-fade overflow-hidden bg-rule-night"
    >
      <span className="sr-only">{label}</span>
      <span className="absolute inset-y-0 w-1/3 animate-sweep bg-sea motion-reduce:animate-none" />
    </div>
  );
}

function Alert({
  language,
  problem,
}: {
  language: Language;
  problem: Problem;
}) {
  return (
    <p role="alert" className="text-lamp">
      {problemText(language, problem)}
    </p>
  );
}

type Divert = (event: { preventDefault: () => void }) => void;

// No stop button: it would only stop the browser's reading, while the run, a confirmed block included, still
// finishes on the Runtime (POL-37).
function Composer({
  language,
  divert,
}: {
  language: Language;
  divert: Divert;
}) {
  const texts = TEXTS[language].chat;
  return (
    <ComposerPrimitive.Root
      onSubmit={divert}
      className="flex items-end gap-2 border border-white/10 glass p-2 pl-5 transition-colors focus-within:border-sea/60"
    >
      <ComposerPrimitive.Input
        aria-label={texts.placeholder}
        placeholder={texts.placeholder}
        maxLength={MAX_MESSAGE}
        rows={1}
        className="max-h-40 min-h-10 flex-1 resize-none bg-transparent py-2 text-base leading-6 outline-none placeholder:text-bone-muted"
      />
      <ComposerPrimitive.Send
        onClick={divert}
        aria-label={texts.send}
        className="flex size-10 shrink-0 items-center justify-center bg-sea text-night transition-colors disabled:bg-white/10 disabled:text-bone-muted"
      >
        <ArrowUp />
      </ComposerPrimitive.Send>
    </ComposerPrimitive.Root>
  );
}

// The empty chat, after assistant-ui's Gemini example (MIT): the question over the composer, the glow beneath it, and
// the numbered suggestions, in the banner's cleared block. Twelve cells wide, so the composer's edges and the
// suggestions' dividers fall on the grid's lines; the conversation keeps the same column. One template for every
// sign-in (ADR-0007, Judges' access): the bar's switch sets the page's language.
function Opening({ language, divert }: { language: Language; divert: Divert }) {
  const texts = TEXTS[language].chat;
  return (
    <Grid layout="chat">
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-8 px-4 py-6 sm:mx-0 sm:ml-[max(1.5rem,calc(50%-6*var(--cell)))] sm:w-[calc(12*var(--cell)+1px)] sm:max-w-[calc(100%-3rem)] sm:px-0">
        <h1 className="text-center text-4xl font-semibold tracking-[-0.035em] text-balance sm:text-5xl">
          {texts.question}
        </h1>
        <div className="relative">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-1/2 -z-10 h-[260px] w-[680px] max-w-[92%] -translate-x-1/2 -translate-y-1/2 animate-light rounded-[140px] glow"
          />
          <Composer language={language} divert={divert} />
        </div>
        <ol className="grid divide-y divide-white/10 border border-white/10 glass sm:grid-cols-3 sm:divide-x sm:divide-y-0">
          {texts.suggestions.map((prompt, index) => (
            <li key={prompt} className="flex">
              <ThreadPrimitive.Suggestion
                prompt={prompt}
                send
                className="flex w-full items-baseline gap-3 px-4 py-3 text-left transition-colors hover:bg-white/5 sm:flex-col sm:items-start sm:gap-2 sm:py-4"
              >
                <span
                  aria-hidden="true"
                  className="font-mono text-xs tracking-[0.08em] text-sea"
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span>{prompt}</span>
              </ThreadPrimitive.Suggestion>
            </li>
          ))}
        </ol>
      </div>
    </Grid>
  );
}

function Thread({
  language,
  problem,
}: {
  language: Language;
  problem: Problem | null;
}) {
  const texts = TEXTS[language].chat;
  const running = useAuiState((state) => state.thread.isRunning);
  const pending = useAgUiInterrupts().length > 0;
  const steerAway = useAgUiSteerAway();
  const aui = useAui();

  // assistant-ui refuses a new message while a control is pending; steerAway settles the control and sends it, and
  // the Runtime decides what the message does to the confirmation (POL-36).
  const divert: Divert = (event) => {
    if (!pending) return;
    event.preventDefault();
    const text = aui.composer.getState().text.trim();
    if (!text) return;
    aui.composer.setText("");
    steerAway(text).catch(() => undefined);
  };

  return (
    <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
      <AuiIf condition={(state) => state.thread.isEmpty}>
        <Opening language={language} divert={divert} />
      </AuiIf>
      <AuiIf condition={(state) => !state.thread.isEmpty}>
        {/* Up under the shell's bar, so the conversation scrolls beneath its glass. */}
        <ThreadPrimitive.Viewport className="-mt-(--bar) flex min-h-0 flex-1 animate-fade flex-col overflow-y-auto pt-(--bar)">
          <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-7 px-4 pt-10 pb-6 sm:max-w-[calc(12*var(--cell)+1px+3rem)] sm:px-6">
            <ThreadPrimitive.Messages>
              {({ message }) =>
                message.role === "user" ? <UserMessage /> : <AssistantMessage />
              }
            </ThreadPrimitive.Messages>
            {running && <Working label={texts.working} />}
          </div>
          <ThreadPrimitive.ViewportFooter className="sticky bottom-0 bg-linear-to-t from-night from-60% to-transparent pt-6 pb-8">
            <div className="mx-auto w-full max-w-3xl px-4 sm:max-w-[calc(12*var(--cell)+1px+3rem)] sm:px-6">
              <Composer language={language} divert={divert} />
            </div>
          </ThreadPrimitive.ViewportFooter>
        </ThreadPrimitive.Viewport>
      </AuiIf>
      {/* Under the composer and outside both views, so the sentence stays put when a failed first message empties
          or fills the thread. */}
      {problem && (
        <div className="mx-auto w-full max-w-3xl px-4 pt-2 sm:max-w-[calc(12*var(--cell)+1px+3rem)] sm:px-6">
          <Alert language={language} problem={problem} />
        </div>
      )}
    </ThreadPrimitive.Root>
  );
}

// While a turn runs, the rail's new conversation waits: the run would still finish on the Runtime, on the runtime
// session the new conversation's warm-up shares, and a confirmed block's outcome would go unseen.
function Running({ onChange }: { onChange: (running: boolean) => void }) {
  const running = useAuiState((state) => state.thread.isRunning);
  useEffect(() => {
    onChange(running);
  }, [running, onChange]);
  return null;
}

function latch(): { opened: Promise<void>; open: () => void } {
  let open: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { opened, open };
}

export function Chat({
  url,
  session,
  language,
  onSignInEnded,
  onRunning,
  fetcher: given,
}: {
  url: string;
  session: string;
  language: Language;
  onSignInEnded: () => void;
  onRunning?: (running: boolean) => void;
  fetcher?: Fetch;
}) {
  const [threadId] = useState(() => crypto.randomUUID());
  const fetcher = useMemo(
    () => given ?? runtimeFetch(session),
    [given, session],
  );
  // A runtime session whose microVM is still starting turns a request away with an error of AgentCore's own, so
  // a message waits for the warm-up to answer.
  const [warm] = useState(latch);
  const agent = useMemo(
    () =>
      createAgent(url, threadId, async (to, init) => {
        await warm.opened;
        return fetcher(to, init);
      }),
    [url, threadId, fetcher, warm],
  );
  const [problem, setProblem] = useState<Problem | null>(null);

  const onError = useCallback(
    (error: Error) => {
      const found = problemOf(error);
      if (found === "signed_out") onSignInEnded();
      else setProblem(found);
    },
    [onSignInEnded],
  );
  const runtime = useAgUiRuntime({ agent, onError });
  const [shown] = useState(() => new Map<string, Shown>());

  useEffect(() => {
    warmUp(url, threadId, fetcher)
      .catch((error: unknown) => {
        if (problemOf(error) === "signed_out") onSignInEnded();
      })
      .finally(warm.open);
  }, [url, threadId, fetcher, onSignInEnded, warm]);

  useEffect(() => {
    const { unsubscribe } = agent.subscribe({
      onRunInitialized: () => {
        setProblem(null);
      },
    });
    return unsubscribe;
  }, [agent]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {onRunning && <Running onChange={onRunning} />}
      <ShownControls value={shown}>
        <Thread language={language} problem={problem} />
      </ShownControls>
    </AssistantRuntimeProvider>
  );
}
