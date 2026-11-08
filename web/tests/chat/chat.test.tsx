import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { Chat } from "../../src/chat/chat";
import { TEXTS } from "../../src/texts";
import { errors, type Event, example, sse } from "../contract";

vi.mock(import("../../src/auth"), async (original) => ({
  ...(await original()),
  accessToken: vi.fn(() => Promise.resolve("customer-access-token")),
}));

const URL =
  "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/r/invocations";
const SESSION = "a".repeat(64);
const EVENTS = example<Event>("event");
function first(type: string): Event {
  const found = EVENTS.find((event) => event.type === type);
  if (!found) throw new Error(`the examples hold no ${type}`);
  return found;
}
const REPLY = first("TEXT_MESSAGE_CONTENT") as Event & {
  messageId: string;
  delta: string;
};
const RATE_LIMITED = first("RUN_ERROR") as Event & { message: string };

interface Request {
  threadId: string;
  runId: string;
  messages: { id: string; role: string; content: unknown }[];
  forwardedProps?: { warmup?: boolean };
}

interface Call {
  url: string;
  headers: Headers;
  body: Request;
}

// The Runtime as the chat's contract describes it: the reply whole, then the snapshot with the masked message.
function turn(request: Request, masked: string): Event[] {
  const last = request.messages.at(-1);
  if (!last) throw new Error("a turn needs a message");
  const ids = { threadId: request.threadId, runId: request.runId };
  const events: Event[] = [
    { type: "RUN_STARTED", ...ids },
    {
      type: "TEXT_MESSAGE_START",
      messageId: REPLY.messageId,
      role: "assistant",
    },
    {
      type: "TEXT_MESSAGE_CONTENT",
      messageId: REPLY.messageId,
      delta: REPLY.delta,
    },
    { type: "TEXT_MESSAGE_END", messageId: REPLY.messageId },
    {
      type: "MESSAGES_SNAPSHOT",
      messages: [
        { id: last.id, role: "user", content: masked },
        { id: REPLY.messageId, role: "assistant", content: REPLY.delta },
      ],
    },
    { type: "RUN_FINISHED", ...ids, outcome: { type: "success" } },
  ];
  for (const event of events) expect(errors("event", event)).toEqual([]);
  return events;
}

function runtime(
  answer: (request: Request) => Response | Promise<Response>,
  warm: Promise<void> = Promise.resolve(),
) {
  const calls: Call[] = [];
  const fetch = vi.fn((url: string, init: RequestInit) => {
    const body = JSON.parse(init.body as string) as Request;
    calls.push({ url, headers: new Headers(init.headers), body });
    if (body.forwardedProps?.warmup) {
      return warm.then(() =>
        sse([
          { type: "RUN_STARTED", threadId: body.threadId, runId: body.runId },
          { type: "RUN_FINISHED", threadId: body.threadId, runId: body.runId },
        ]),
      );
    }
    return Promise.resolve(answer(body));
  });
  vi.stubGlobal("fetch", fetch);
  return calls;
}

function chat(language: "es" | "pt" = "pt", onSignInEnded = vi.fn()) {
  render(
    <Chat
      url={URL}
      session={SESSION}
      language={language}
      onSignInEnded={onSignInEnded}
    />,
  );
  return onSignInEnded;
}

async function send(text: string, language: "es" | "pt" = "pt") {
  const user = userEvent.setup();
  await user.type(
    screen.getByLabelText(TEXTS[language].chat.placeholder),
    text,
  );
  await user.click(
    screen.getByRole("button", { name: TEXTS[language].chat.send }),
  );
}

beforeEach(() => {
  vi.stubGlobal("crypto", globalThis.crypto);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a turn", () => {
  test("renders the reply sent whole once, and the customer's message as the snapshot masked it", async () => {
    const typed = "Perdi meu cartão 4111 1111 1111 4821, quero bloquear.";
    const masked = "Perdi meu cartão ****4821, quero bloquear.";
    runtime((request) => sse(turn(request, masked)));
    chat();

    await send(typed);

    expect(await screen.findByText(REPLY.delta)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(masked)).toBeInTheDocument();
    });
    expect(screen.queryByText(typed)).not.toBeInTheDocument();
    expect(screen.getAllByText(REPLY.delta)).toHaveLength(1);
  });

  test("sends requests the chat's contract accepts, with the token and the runtime session (SEC-04)", async () => {
    const calls = runtime((request) =>
      sse(turn(request, "Quais cartões eu tenho?")),
    );
    chat();

    await send("Quais cartões eu tenho?");
    await screen.findByText(REPLY.delta);

    const [warmup, message] = calls;
    expect(warmup?.body.forwardedProps).toEqual({ warmup: true });
    expect(message?.body.messages.at(-1)?.content).toBe(
      "Quais cartões eu tenho?",
    );
    for (const call of calls) {
      expect(errors("request", call.body)).toEqual([]);
      expect(call.url).toBe(URL);
      expect(call.headers.get("Authorization")).toBe(
        "Bearer customer-access-token",
      );
      expect(
        call.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"),
      ).toBe(SESSION);
    }
    expect(warmup?.body.threadId).toBe(message?.body.threadId);
  });

  test("waits for the warm-up to answer, since a runtime session still starting turns a message away", async () => {
    let answerWarmUp: (() => void) | undefined;
    const calls = runtime(
      (request) => sse(turn(request, "Quais cartões eu tenho?")),
      new Promise<void>((resolve) => {
        answerWarmUp = resolve;
      }),
    );
    chat();

    await send("Quais cartões eu tenho?");
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(calls).toHaveLength(1);
    answerWarmUp?.();

    expect(await screen.findByText(REPLY.delta)).toBeInTheDocument();
    expect(calls).toHaveLength(2);
  });
});

describe("a run that fails", () => {
  test.each(["es", "pt"] as const)(
    "shows the fixed sentence for its code, never the event's message (%s)",
    async (language) => {
      runtime((request) =>
        sse([
          {
            type: "RUN_STARTED",
            threadId: request.threadId,
            runId: request.runId,
          },
          RATE_LIMITED,
        ]),
      );
      chat(language);

      await send("Hola", language);

      expect(
        await screen.findByText(TEXTS[language].chat.problems.rate_limited),
      ).toBeInTheDocument();
      expect(screen.queryByText(RATE_LIMITED.message)).not.toBeInTheDocument();
    },
  );

  test("says Faro couldn't be reached when the network fails", async () => {
    runtime(() => Promise.reject(new TypeError("Failed to fetch")));
    chat();

    await send("Oi");

    expect(
      await screen.findByText(TEXTS.pt.chat.problems.unreachable),
    ).toBeInTheDocument();
  });

  test("ends the sign-in when the Runtime turns the token away (POL-09)", async () => {
    runtime(() => sse([], 401));
    const onSignInEnded = chat();

    await send("Oi");

    await waitFor(() => {
      expect(onSignInEnded).toHaveBeenCalled();
    });
  });

  test("ends the sign-in when the warm-up is turned away", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(sse([], 401))),
    );
    const onSignInEnded = chat();

    await waitFor(() => {
      expect(onSignInEnded).toHaveBeenCalled();
    });
  });

  test("clears the sentence when the next run starts", async () => {
    let fail = true;
    runtime((request) => {
      if (!fail) return sse(turn(request, "Oi de novo"));
      fail = false;
      return Promise.reject(new TypeError("Failed to fetch"));
    });
    chat();

    await send("Oi");
    await screen.findByText(TEXTS.pt.chat.problems.unreachable);
    await send("Oi de novo");

    await screen.findByText(REPLY.delta);
    expect(
      screen.queryByText(TEXTS.pt.chat.problems.unreachable),
    ).not.toBeInTheDocument();
  });
});

test("tells the page while a turn runs, so a new conversation waits for it", async () => {
  const onRunning = vi.fn();
  let answer: () => void = () => undefined;
  const answered = new Promise<void>((resolve) => {
    answer = resolve;
  });
  runtime(async (request) => {
    await answered;
    return sse(turn(request, "Oi"));
  });
  render(
    <Chat
      url={URL}
      session={SESSION}
      language="pt"
      onSignInEnded={vi.fn()}
      onRunning={onRunning}
    />,
  );

  await send("Oi");

  await waitFor(() => {
    expect(onRunning).toHaveBeenLastCalledWith(true);
  });
  answer();
  await screen.findByText(REPLY.delta);
  await waitFor(() => {
    expect(onRunning).toHaveBeenLastCalledWith(false);
  });
});

test("every sign-in opens on the same template: the suggestions in one ruled row, and no card", () => {
  runtime(() => sse([]));
  chat("pt");

  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  expect(screen.getByRole("list")).toHaveClass(
    "divide-y",
    "sm:grid-cols-3",
    "sm:divide-x",
    "sm:divide-y-0",
  );
});

test("opens on Faro's question, with three numbered prompts that send themselves", async () => {
  const texts = TEXTS.es.chat;
  const [, block] = texts.suggestions;
  const calls = runtime((request) => sse(turn(request, block)));
  chat("es");

  expect(
    screen.getByRole("heading", { name: texts.question }),
  ).toBeInTheDocument();
  for (const prompt of texts.suggestions) {
    expect(screen.getByRole("button", { name: prompt })).toBeInTheDocument();
  }
  await userEvent.setup().click(screen.getByRole("button", { name: block }));

  await screen.findByText(REPLY.delta);
  expect(calls.at(-1)?.body.messages.at(-1)?.content).toBe(block);
  expect(
    screen.queryByRole("heading", { name: texts.question }),
  ).not.toBeInTheDocument();
  expect(screen.getByLabelText(texts.placeholder)).toBeInTheDocument();
});
