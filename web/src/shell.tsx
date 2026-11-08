import { type ReactNode, useState } from "react";

import type { Language } from "./contracts/chat";
import { PanelLeft } from "./icons";
import { TEXTS } from "./texts";

// One button per action, whatever the width: a label beside the icon in the open rail, a label for screen readers
// otherwise, so a test or a reader finds each action once.
export function RailButton({
  icon,
  label,
  expanded,
  disabled = false,
  className = "",
  onClick,
}: {
  icon: ReactNode;
  label: string;
  expanded: boolean;
  disabled?: boolean;
  className?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`flex h-10 items-center gap-3 px-2.5 text-bone-muted transition-colors hover:bg-white/5 hover:text-bone disabled:opacity-40 disabled:hover:bg-transparent ${className}`}
    >
      {icon}
      <span
        className={expanded ? "sr-only sm:not-sr-only sm:truncate" : "sr-only"}
      >
        {label}
      </span>
    </button>
  );
}

// The language switch, flush right in the bar (ADR-0007, Routes): mono capitals, each option named in its own
// language, the chosen one in full color and the other muted. It only sets the page's own text; the agent's replies
// follow the conversation (POL-50).
function LanguageSwitch({
  language,
  onLanguage,
}: {
  language: Language;
  onLanguage: (language: Language) => void;
}) {
  const texts = TEXTS[language].language;
  return (
    <div
      role="group"
      aria-label={texts.label}
      className="ml-auto flex shrink-0 items-center"
    >
      {(["es", "pt"] as const).map((option) => (
        <button
          key={option}
          type="button"
          lang={option}
          aria-pressed={option === language}
          aria-label={texts[option]}
          onClick={() => {
            onLanguage(option);
          }}
          className={`flex h-8 items-center px-2.5 font-mono text-xs tracking-[0.08em] transition-colors ${
            option === language
              ? "text-bone"
              : "text-bone-muted hover:bg-white/5 hover:text-bone"
          }`}
        >
          {option.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

// The site's pages, the customer's and the console's: the rail once signed in (its icons in the bar on phones), the
// bar, and the synthetic-data notice on every page (SEC-02). A sign-in's bar carries the bank's name, its notice flush
// right; once signed in, the bar carries Faro's wordmark, the notice centered. The console's bar reads the bank's name
// and the console's, signed in or not. The rail and the bar are glass over the page, which runs on under them; main's
// padding keeps the content clear of them, by --rail and --bar. --cell, the grid's module, sets the pages' column
// widths (the identity guide's Layout). The frame fades in as a page arrives (the identity guide's Motifs). A page may
// bring its own footer in the notice's place, as the customers' sign-in does with what we keep.
export function Shell({
  language,
  label,
  signIn = false,
  note,
  rail,
  running = false,
  onNew,
  onLanguage,
  footer,
  children,
}: {
  language: Language;
  label?: string;
  signIn?: boolean;
  note?: string;
  rail?: (expanded: boolean) => ReactNode;
  running?: boolean;
  onNew?: () => void;
  onLanguage?: (language: Language) => void;
  footer?: ReactNode;
  children: ReactNode;
}) {
  const texts = TEXTS[language];
  const [expanded, setExpanded] = useState(false);
  const width = rail
    ? expanded
      ? "sm:[--rail:16rem]"
      : "sm:[--rail:3.5rem]"
    : "";
  return (
    <div
      lang={language}
      className={`relative flex h-dvh flex-col [--bar:3.5rem] [--cell:32px] [--rail:0px] sm:[--cell:51px] ${width}`}
    >
      {rail && (
        <nav
          aria-label={texts.rail.label}
          className="fixed top-1 right-2 z-20 flex animate-fade gap-1 p-1 sm:absolute sm:inset-y-0 sm:left-0 sm:w-(--rail) sm:flex-col sm:border-r sm:border-white/10 sm:glass-thin sm:px-2 sm:py-2"
        >
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={expanded ? texts.rail.close : texts.rail.open}
            onClick={() => {
              setExpanded(!expanded);
            }}
            className="hidden h-10 w-10 items-center justify-center text-bone-muted hover:bg-white/5 hover:text-bone sm:flex"
          >
            <PanelLeft />
          </button>
          {rail(expanded)}
        </nav>
      )}
      <header
        className={`absolute top-0 right-0 left-(--rail) z-10 flex h-(--bar) animate-fade items-center border-b border-white/10 glass-thin px-4 sm:px-6 ${rail ? "max-sm:pr-28" : ""}`}
      >
        {/* On phones, clear of the rail's icons, which sit in the bar. */}
        <div className="flex min-w-0 items-center gap-3">
          {signIn || label ? (
            <p className="shrink-0 animate-fade text-lg font-medium tracking-[-0.01em]">
              LATAM Bank
            </p>
          ) : (
            // The rail's new conversation, in place rather than by a reload, so it waits while a turn runs as that one
            // does; without it (the broken page), the link reloads.
            <a
              href="/chat"
              aria-disabled={running || undefined}
              onClick={(event) => {
                if (!onNew || event.metaKey || event.ctrlKey || event.shiftKey)
                  return;
                event.preventDefault();
                if (!running) onNew();
              }}
              className="animate-fade font-display text-2xl font-semibold tracking-[-0.03em] aria-disabled:cursor-default"
            >
              Faro
            </a>
          )}
          {label && (
            <>
              <span
                aria-hidden="true"
                className="h-5 w-px shrink-0 bg-white/20"
              />
              <p className="truncate text-lg tracking-[-0.01em] text-bone-muted">
                {label}
              </p>
            </>
          )}
        </div>
        {onLanguage && (
          <LanguageSwitch language={language} onLanguage={onLanguage} />
        )}
      </header>
      <main className="flex min-h-0 flex-1 flex-col overflow-y-auto pt-(--bar) pl-(--rail)">
        {children}
      </main>
      {footer ?? (
        <footer
          className={`pt-2 pr-4 pb-5 pl-[calc(var(--rail)+1rem)] ${signIn ? "text-right" : "text-center"} text-xs text-balance text-bone-muted sm:pr-6 sm:pl-[calc(var(--rail)+1.5rem)]`}
        >
          <p key={signIn ? "sign-in" : "signed-in"} className="animate-fade">
            {/* Two sentences once signed in: the synthetic-data label (SEC-02) and the session's end. */}
            <span>{note ? texts.noticeBrief : texts.notice}</span>
            {note && <span> {note}</span>}
          </p>
        </footer>
      )}
    </div>
  );
}
