import { useEffect, useId, useRef, useState } from "react";

import type { Language } from "./contracts/chat";
import type { Welcome } from "./contracts/guest";
import { ChevronUp, LogOut, UserRound } from "./icons";
import { TEXTS } from "./texts";

type Shown = "closed" | "open" | "closing";

// The rail's foot: who a guest entered as, and behind it their cards and the sign-out. The menu unfolds upward from the
// rail and downward from a phone's bar, and folds away when a click or Escape lands outside it. An invited customer,
// whose name and cards the page doesn't hold, is the account alone.
export function Account({
  language,
  welcome,
  expanded,
  onSignOut,
}: {
  language: Language;
  welcome: Welcome | null;
  expanded: boolean;
  onSignOut: () => void;
}) {
  const texts = TEXTS[language];
  const name = welcome?.name ?? null;
  const cards = welcome?.cards ?? [];
  const listed = useId();
  const [shown, setShown] = useState<Shown>("closed");
  const menu = useId();
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const title = name ?? texts.account.label;

  useEffect(() => {
    if (shown !== "open") return;
    const away = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setShown("closing");
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setShown("closing");
      button.current?.focus();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [shown]);

  useEffect(() => {
    const element = panel.current;
    if (shown !== "closing" || !element) return;
    let current = true;
    void Promise.allSettled(
      element.getAnimations().map((animation) => animation.finished),
    ).then(() => {
      if (current) setShown("closed");
    });
    return () => {
      current = false;
    };
  }, [shown]);

  return (
    <div ref={root} className="relative sm:mt-auto">
      <button
        ref={button}
        type="button"
        aria-expanded={shown === "open"}
        aria-controls={menu}
        aria-label={name ? `${texts.account.label}: ${name}` : title}
        onClick={() => {
          setShown(shown === "open" ? "closing" : "open");
        }}
        className="flex w-full items-center gap-3 p-1 text-left transition-colors duration-300 hover:bg-white/5"
      >
        <span className="flex size-8 shrink-0 items-center justify-center border border-white/10 bg-white/5 text-bone-muted">
          <UserRound />
        </span>
        <span
          className={expanded ? "min-w-0 flex-1 max-sm:sr-only" : "sr-only"}
        >
          <span className="block truncate text-sm text-bone">{title}</span>
          <span className="block truncate text-xs text-bone-muted">
            {texts.account.fictional}
          </span>
        </span>
        {expanded && (
          <span
            className={`mr-1 text-bone-muted transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none max-sm:hidden ${shown === "open" ? "" : "rotate-180"}`}
          >
            <ChevronUp />
          </span>
        )}
      </button>
      {shown !== "closed" && (
        <div
          ref={panel}
          id={menu}
          className={`absolute top-full right-0 z-30 mt-2 w-60 border border-white/10 glass p-1.5 [--rise:0px] motion-safe:[--rise:-6px] sm:top-auto sm:right-auto sm:bottom-full sm:left-0 sm:mt-0 sm:mb-2 sm:motion-safe:[--rise:6px] ${shown === "closing" ? "animate-fold" : "animate-unfold"}`}
        >
          {/* The name shows here only where the button hides it: on a phone, or in the closed rail. */}
          <div className={`px-2.5 pt-2 pb-1 ${expanded ? "sm:hidden" : ""}`}>
            <p className="truncate text-sm text-bone">{title}</p>
            <p className="truncate text-xs text-bone-muted">
              {texts.account.fictional}
            </p>
          </div>
          {cards.length > 0 && (
            <div className="px-2.5 pt-2 pb-1.5">
              <p
                id={listed}
                className="font-mono text-[11px] tracking-[0.08em] text-bone-muted uppercase"
              >
                {texts.account.cards}
              </p>
              <ul aria-labelledby={listed} className="mt-1.5 flex flex-col">
                {/* Two cards of a type can share the last four digits (POL-15), so the key is the place. */}
                {cards.map((card, index) => (
                  <li
                    key={index}
                    className="flex items-baseline justify-between gap-3 py-1 text-sm"
                  >
                    <span className="text-bone">
                      {texts.account.type[card.product_type]}
                    </span>{" "}
                    <span className="font-mono text-xs text-bone-muted">
                      <span aria-hidden="true">••••</span>
                      <span className="sr-only">
                        {" "}
                        {texts.account.ending}
                      </span>{" "}
                      {card.last_four}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div aria-hidden="true" className="my-1 h-px bg-white/10" />
          <button
            type="button"
            onClick={onSignOut}
            className="flex h-10 w-full items-center gap-3 px-2.5 text-sm text-bone-muted transition-colors hover:bg-white/5 hover:text-bone"
          >
            <LogOut />
            {texts.signIn.signOut}
          </button>
        </div>
      )}
    </div>
  );
}
