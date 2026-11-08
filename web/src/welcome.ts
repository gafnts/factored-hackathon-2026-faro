import type { Welcome, WelcomeCard } from "./contracts/guest";

const KEY = "faro.welcome";
const TYPES: readonly string[] = ["Tarjeta Crédito", "Tarjeta Débito"];

// Who a guest entered as, from the guest entry's answer (ADR-0007, The guest entry). It names a synthetic customer and
// their cards' last four digits, and lives beside the tab's tokens, dropped with them.
export function welcomeOf(value: unknown): Welcome | null {
  if (typeof value !== "object" || value === null) return null;
  const { name, cards } = value as Record<string, unknown>;
  if (typeof name !== "string" || !name.trim()) return null;
  if (cards === null) return { name, cards: null };
  if (!Array.isArray(cards)) return null;
  const read = cards.map((card: unknown): WelcomeCard | null => {
    if (typeof card !== "object" || card === null) return null;
    const { product_type: type, last_four: lastFour } = card as Record<
      string,
      unknown
    >;
    return typeof type === "string" &&
      TYPES.includes(type) &&
      typeof lastFour === "string" &&
      /^[0-9]{4}$/.test(lastFour)
      ? {
          product_type: type as WelcomeCard["product_type"],
          last_four: lastFour,
        }
      : null;
  });
  const kept = read.filter((card) => card !== null);
  return kept.length === read.length ? { name, cards: kept } : null;
}

export function keepWelcome(welcome: Welcome): void {
  window.sessionStorage.setItem(KEY, JSON.stringify(welcome));
}

export function readWelcome(): Welcome | null {
  const kept = window.sessionStorage.getItem(KEY);
  if (kept === null) return null;
  try {
    return welcomeOf(JSON.parse(kept));
  } catch {
    return null;
  }
}

export function dropWelcome(): void {
  window.sessionStorage.removeItem(KEY);
}
