import { expect, test } from "vitest";

import { isRunErrorCode, problemText, TEXTS } from "../src/texts";
import { errors } from "./contract";

test.each(["es", "pt"] as const)(
  "every problem has a sentence in %s",
  (language) => {
    for (const text of Object.values(TEXTS[language].chat.problems)) {
      const sentence = typeof text === "string" ? text : text("18:00");
      expect(sentence.length).toBeGreaterThan(0);
    }
  },
);

test.each([
  ["2026-11-07T23:59:00Z", Date.UTC(2026, 10, 8)],
  ["2026-11-08T00:00:00Z", Date.UTC(2026, 10, 9)],
  ["2026-11-08T05:30:00Z", Date.UTC(2026, 10, 9)],
])(
  "a day-long refusal at %s names the next UTC midnight in the browser's time",
  (now, end) => {
    for (const language of ["es", "pt"] as const) {
      const time = new Intl.DateTimeFormat(language, {
        timeStyle: "short",
      }).format(end);

      expect(problemText(language, "global_limit", new Date(now))).toBe(
        TEXTS[language].chat.problems.global_limit(time),
      );
      expect(problemText(language, "daily_limit", new Date(now))).toBe(
        TEXTS[language].chat.problems.daily_limit(time),
      );
    }
  },
);

test("the day's end reads naturally in both languages", () => {
  const { es, pt } = { es: TEXTS.es.chat.problems, pt: TEXTS.pt.chat.problems };

  expect(es.daily_limit("18:00")).toBe(
    "Alcanzó el límite de mensajes de hoy. Vuelva a intentarlo a partir de las 18:00.",
  );
  expect(es.global_limit("1:00")).toBe(
    "Faro alcanzó su límite de mensajes de hoy. Vuelva a intentarlo a partir de la 1:00.",
  );
  expect(pt.daily_limit("21:00")).toBe(
    "Você atingiu o limite de mensagens de hoje. Tente novamente a partir das 21:00.",
  );
  expect(pt.global_limit("01:00")).toBe(
    "O Faro atingiu o limite de mensagens de hoje. Tente novamente a partir da 01:00.",
  );
});

test("the pause names no time", () => {
  for (const language of ["es", "pt"] as const) {
    expect(problemText(language, "paused")).not.toMatch(/\d/);
  }
});

test("the fixed sentences cover exactly the contract's error codes", () => {
  const codes = Object.keys(TEXTS.es.chat.problems).filter(isRunErrorCode);

  for (const code of codes) {
    expect(
      errors("run_error", { type: "RUN_ERROR", message: "x", code }),
    ).toEqual([]);
  }
  expect(
    errors("run_error", {
      type: "RUN_ERROR",
      message: "x",
      code: "unreachable",
    }),
  ).not.toEqual([]);
  expect(isRunErrorCode("unreachable")).toBe(false);
  expect(isRunErrorCode(42)).toBe(false);
});

test("the session's end reads naturally in both languages", () => {
  expect(TEXTS.es.signIn.endsAt("15:42")).toBe(
    "Su sesión terminará a las 15:42.",
  );
  expect(TEXTS.pt.signIn.endsAt("15:42")).toBe(
    "Sua sessão terminará às 15:42.",
  );
});
