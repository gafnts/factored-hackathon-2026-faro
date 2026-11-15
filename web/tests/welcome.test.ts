import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import type { GuestEntry } from "../src/contracts/guest";
import {
  dropWelcome,
  keepWelcome,
  readWelcome,
  welcomeOf,
} from "../src/welcome";

const EXAMPLES = JSON.parse(
  readFileSync(
    resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../../src/faro/contracts/examples/guest.json",
    ),
    "utf8",
  ),
) as GuestEntry[];
const WELCOMES = EXAMPLES.flatMap((entry) =>
  "welcome" in entry ? [entry.welcome] : [],
);

test.each(WELCOMES)(
  "a welcome the contract allows is kept and read back as given (ADR-0007, The guest entry)",
  (welcome) => {
    expect(welcomeOf(welcome)).toEqual(welcome);
    keepWelcome(welcome);
    expect(readWelcome()).toEqual(welcome);
    dropWelcome();
    expect(readWelcome()).toBeNull();
  },
);

test.each([
  ["no object", "Team Customer"],
  ["no name", { cards: null }],
  ["a blank name", { name: " ", cards: null }],
  ["no cards", { name: "Team Customer" }],
  ["cards that aren't a list", { name: "Team Customer", cards: "4821" }],
  [
    "a card of another type",
    {
      name: "Team Customer",
      cards: [{ product_type: "Cuenta Ahorro", last_four: "4821" }],
    },
  ],
  [
    "more than four digits",
    {
      name: "Team Customer",
      cards: [{ product_type: "Tarjeta Crédito", last_four: "48210" }],
    },
  ],
])("a welcome with %s isn't shown", (_, value) => {
  expect(welcomeOf(value)).toBeNull();
});

test("a kept welcome that isn't JSON reads as none", () => {
  window.sessionStorage.setItem("faro.welcome", "{");

  expect(readWelcome()).toBeNull();
});
