import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  currentSignIn,
  enterAsGuest,
  signInWith,
  signOutHere,
} from "../src/auth";
import { loadConfig } from "../src/config";
import { Customer } from "../src/customer";
import { drawRuntimeSession } from "../src/session";
import { dayEnds, TEXTS } from "../src/texts";

vi.mock(import("../src/config"), async (original) => ({
  ...(await original()),
  loadConfig: vi.fn(),
}));

vi.mock(import("../src/auth"), async (original) => ({
  ...(await original()),
  configureAuth: vi.fn(),
  currentSignIn: vi.fn(),
  enterAsGuest: vi.fn(),
  signInWith: vi.fn(),
  signOutHere: vi.fn(() => Promise.resolve()),
}));

// Counts the chat's mounts, since each new conversation mounts it again.
const chats = vi.hoisted(() => ({ mounted: 0 }));

vi.mock(import("../src/chat/chat"), async () => {
  const { useState } = await import("react");
  return {
    Chat: ({
      session,
      onSignInEnded,
      onRunning,
    }: {
      session: string;
      onSignInEnded: () => void;
      onRunning?: (running: boolean) => void;
    }) => {
      const [mount] = useState(() => (chats.mounted += 1));
      return (
        <div>
          <p>chat in {session}</p>
          <p>conversation {mount}</p>
          <button type="button" onClick={onSignInEnded}>
            runtime turned the token away
          </button>
          <button type="button" onClick={() => onRunning?.(true)}>
            a turn starts
          </button>
          <button type="button" onClick={() => onRunning?.(false)}>
            the turn ends
          </button>
        </div>
      );
    },
  };
});

const CONFIG = {
  region: "us-east-1",
  user_pool_id: "us-east-1_pool",
  customer_client_id: "customers-client",
  staff_client_id: "staff-client",
  runtime_url: "https://runtime.example",
};
const texts = TEXTS.es;

function signedIn(endsIn = 30 * 60 * 1000) {
  return { sub: "sub", endsAt: Date.now() + endsIn };
}

// The form waits behind a quiet button, since the guest entry leads (ADR-0007, Sign-in).
async function signInThroughTheForm() {
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: texts.signIn.guest.withAccount }),
  );
  await user.type(
    await screen.findByLabelText(texts.signIn.username),
    " persona ",
  );
  await user.type(screen.getByLabelText(texts.signIn.password), "secret");
  await user.click(screen.getByRole("button", { name: texts.signIn.submit }));
}

beforeEach(() => {
  vi.mocked(loadConfig).mockResolvedValue(CONFIG);
  vi.mocked(currentSignIn).mockResolvedValue(null);
});

afterEach(() => {
  vi.clearAllMocks();
});

test("says the app couldn't load when config.json can't be read", async () => {
  vi.mocked(loadConfig).mockRejectedValue(new Error("x"));
  render(<Customer language="es" />);

  expect(await screen.findByText(texts.broken)).toBeInTheDocument();
});

test("signs in through the form and opens the chat in a runtime session of its own", async () => {
  vi.mocked(signInWith).mockResolvedValue("signed_in");
  vi.mocked(currentSignIn)
    .mockResolvedValueOnce(null)
    .mockResolvedValue(signedIn());
  render(<Customer language="es" />);

  expect(await screen.findByRole("banner")).toHaveTextContent(/^LATAM Bank/);
  await signInThroughTheForm();

  expect(await screen.findByText(/^chat in [0-9a-f]{64}$/)).toBeInTheDocument();
  expect(screen.getByRole("banner")).toHaveTextContent(/^Faro/);
  expect(signInWith).toHaveBeenCalledWith("persona", "secret");
  expect(screen.getByText(/Su sesión terminará a las/)).toBeInTheDocument();
  // Signed in, the footer keeps to two sentences: the brief label and the session's end.
  expect(screen.getByText(texts.noticeBrief)).toBeInTheDocument();
  expect(screen.queryByText(texts.notice)).not.toBeInTheDocument();
});

test("the sign-in says what we keep in the notice's place, and its dialog lists it all (ADR-0007, Sign-in)", async () => {
  render(<Customer language="es" />);

  const footer = await screen.findByRole("contentinfo");
  expect(footer).toHaveTextContent(texts.signIn.terms.banner);
  expect(screen.queryByText(texts.notice)).not.toBeInTheDocument();
  const dialog = screen.getByRole("dialog", { hidden: true });
  expect(dialog).not.toHaveAttribute("open");

  const user = userEvent.setup();
  await user.click(
    within(footer).getByRole("button", { name: texts.signIn.terms.more }),
  );

  expect(dialog).toHaveAttribute("open");
  expect(dialog).toHaveAccessibleName(texts.signIn.terms.title);
  const lines = within(dialog).getAllByRole("listitem");
  expect(lines.map((line) => line.textContent)).toEqual(
    texts.signIn.terms.lines,
  );
  await user.click(
    within(dialog).getByRole("button", { name: texts.signIn.terms.close }),
  );
  expect(dialog).not.toHaveAttribute("open");
});

test("the bar's switch sets the page's language, starting from the browser's", async () => {
  render(<Customer language="es" />);

  expect(
    await screen.findByRole("heading", { name: texts.signIn.guest.title }),
  ).toBeInTheDocument();
  const options = screen.getByRole("group", { name: texts.language.label });
  expect(
    within(options).getByRole("button", { name: texts.language.es }),
  ).toHaveAttribute("aria-pressed", "true");
  await userEvent
    .setup()
    .click(within(options).getByRole("button", { name: texts.language.pt }));

  expect(
    screen.getByRole("heading", { name: TEXTS.pt.signIn.guest.title }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: texts.language.pt }),
  ).toHaveAttribute("aria-pressed", "true");
});

test.each([
  ["refused", texts.signIn.refused],
  ["unreachable", texts.signIn.unreachable],
] as const)(
  "a sign-in %s shows its fixed sentence",
  async (outcome, sentence) => {
    vi.mocked(signInWith).mockResolvedValue(outcome);
    render(<Customer language="es" />);

    await signInThroughTheForm();

    expect(await screen.findByRole("alert")).toHaveTextContent(sentence);
    expect(
      screen.getByRole("button", { name: texts.signIn.submit }),
    ).toBeEnabled();
  },
);

test("a sign-in that leaves no readable token is refused", async () => {
  vi.mocked(signInWith).mockResolvedValue("signed_in");
  render(<Customer language="es" />);

  await signInThroughTheForm();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    texts.signIn.refused,
  );
});

test("a reload keeps the sign-in and its runtime session", async () => {
  const session = drawRuntimeSession();
  vi.mocked(currentSignIn).mockResolvedValue(signedIn());
  render(<Customer language="es" />);

  expect(await screen.findByText(`chat in ${session}`)).toBeInTheDocument();
});

test("a new conversation mounts the chat again in the same runtime session, once no turn runs", async () => {
  const session = drawRuntimeSession();
  vi.mocked(currentSignIn).mockResolvedValue(signedIn());
  render(<Customer language="es" />);
  const user = userEvent.setup();

  const first = (await screen.findByText(/^conversation \d+$/)).textContent;
  const newChat = screen.getByRole("button", { name: texts.rail.newChat });
  await user.click(screen.getByRole("button", { name: "a turn starts" }));
  expect(newChat).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "the turn ends" }));
  await user.click(newChat);

  expect(screen.getByText(/^conversation \d+$/).textContent).not.toBe(first);
  expect(screen.getByText(`chat in ${session}`)).toBeInTheDocument();
});

test("the wordmark starts a new conversation too, without leaving the page", async () => {
  vi.mocked(currentSignIn).mockResolvedValue(signedIn());
  render(<Customer language="es" />);

  const first = (await screen.findByText(/^conversation \d+$/)).textContent;
  await userEvent.setup().click(screen.getByRole("link", { name: "Faro" }));

  expect(screen.getByText(/^conversation \d+$/).textContent).not.toBe(first);
});

test("a reload after the hour ends the sign-in", async () => {
  vi.mocked(currentSignIn).mockResolvedValue(signedIn(-1));
  render(<Customer language="es" />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    texts.signIn.ended,
  );
  expect(signOutHere).toHaveBeenCalled();
});

test("the hour's end signs the tab out and says so (POL-09)", async () => {
  vi.mocked(currentSignIn).mockResolvedValue(signedIn(100));
  render(<Customer language="es" />);
  await screen.findByText(/^chat in/);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    texts.signIn.ended,
  );
  expect(signOutHere).toHaveBeenCalled();
});

test("the Runtime turning the token away ends the sign-in", async () => {
  vi.mocked(currentSignIn).mockResolvedValue(signedIn());
  render(<Customer language="es" />);

  await userEvent.setup().click(
    await screen.findByRole("button", {
      name: "runtime turned the token away",
    }),
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    texts.signIn.ended,
  );
});

test("signing out returns to the guest entry without a message", async () => {
  vi.mocked(currentSignIn).mockResolvedValue(signedIn());
  render(<Customer language="pt" />);

  await userEvent
    .setup()
    .click(
      await screen.findByRole("button", { name: TEXTS.pt.signIn.signOut }),
    );

  expect(
    await screen.findByRole("button", { name: TEXTS.pt.signIn.guest.enter }),
  ).toBeInTheDocument();
  expect(signOutHere).toHaveBeenCalled();
  await waitFor(() => {
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

test("the customers' sign-in leads with the guest entry, and the form waits behind a quiet button (ADR-0007, Sign-in)", async () => {
  render(<Customer language="es" />);
  const user = userEvent.setup();

  const enter = await screen.findByRole("button", {
    name: texts.signIn.guest.enter,
  });
  expect(screen.getByText(texts.signIn.guest.lead)).toBeInTheDocument();
  expect(
    screen.queryByLabelText(texts.signIn.username),
  ).not.toBeInTheDocument();
  expect(enter).not.toHaveFocus();

  await user.click(
    screen.getByRole("button", { name: texts.signIn.guest.withAccount }),
  );
  expect(screen.getByLabelText(texts.signIn.username)).toHaveFocus();

  await user.click(
    screen.getByRole("button", { name: texts.signIn.guest.asGuest }),
  );
  expect(
    screen.getByRole("button", { name: texts.signIn.guest.enter }),
  ).toHaveFocus();
});

test("entering as a guest opens the chat in a runtime session of its own", async () => {
  vi.mocked(enterAsGuest).mockResolvedValue("signed_in");
  vi.mocked(currentSignIn)
    .mockResolvedValueOnce(null)
    .mockResolvedValue(signedIn());
  render(<Customer language="es" />);

  await userEvent
    .setup()
    .click(
      await screen.findByRole("button", { name: texts.signIn.guest.enter }),
    );

  expect(await screen.findByText(/^chat in [0-9a-f]{64}$/)).toBeInTheDocument();
  expect(enterAsGuest).toHaveBeenCalledOnce();
});

test.each([
  ["full", texts.signIn.guest.full(dayEnds("es")), true],
  ["paused", texts.signIn.guest.paused, true],
  ["unreachable", texts.signIn.unreachable, false],
] as const)(
  "a guest entry answered %s says so above the button",
  async (outcome, sentence, held) => {
    vi.mocked(enterAsGuest).mockResolvedValue(outcome);
    render(<Customer language="es" />);

    await userEvent
      .setup()
      .click(
        await screen.findByRole("button", { name: texts.signIn.guest.enter }),
      );

    expect(await screen.findByRole("alert")).toHaveTextContent(sentence);
    const button = screen.getByRole("button", {
      name: texts.signIn.guest.enter,
    });
    if (held) expect(button).toBeDisabled();
    else expect(button).toBeEnabled();
  },
);

test("a guest entry that leaves no readable token can't be reached", async () => {
  vi.mocked(enterAsGuest).mockResolvedValue("signed_in");
  render(<Customer language="es" />);

  await userEvent
    .setup()
    .click(
      await screen.findByRole("button", { name: texts.signIn.guest.enter }),
    );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    texts.signIn.unreachable,
  );
});
