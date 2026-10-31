import { HttpAgent, type RunAgentInput } from "@ag-ui/client";

import { accessToken, SignInEndedError } from "./auth";
import { isRunErrorCode, type Problem } from "./texts";

export type Fetch = (url: string, init: RequestInit) => Promise<Response>;

// Asked for at every call, so a refreshed token never leaves a stale header behind.
export function runtimeFetch(
  session: string,
  token: () => Promise<string> = accessToken,
): Fetch {
  return async (url, init) => {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${await token()}`);
    headers.set("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id", session);
    const response = await fetch(url, { ...init, headers });
    if (response.status === 401) throw new SignInEndedError();
    return response;
  };
}

// A message typed while a control shows goes out through assistant-ui's steerAway, which adds a cancelled entry
// without a payload for each open interrupt. The chat's contract takes a resume only as a control's answer, so the
// message goes alone, and the Runtime turns it into a resume of its own (ADR-0004, The confirmation).
export function withoutSteerAway(input: RunAgentInput): RunAgentInput {
  const entries = input.resume ?? [];
  const steered =
    entries.length > 0 &&
    input.messages.at(-1)?.role === "user" &&
    entries.every(
      (entry) => entry.status === "cancelled" && entry.payload === undefined,
    );
  if (!steered) return input;
  const message = { ...input };
  delete message.resume;
  return message;
}

export class ChatAgent extends HttpAgent {
  protected override requestInit(input: RunAgentInput): RequestInit {
    return super.requestInit(withoutSteerAway(input));
  }
}

export function createAgent(
  url: string,
  threadId: string,
  fetcher: Fetch,
): HttpAgent {
  return new ChatAgent({ url, threadId, fetch: fetcher });
}

// AgentCore turns a request away with a RUN_ERROR of its own while the runtime session's microVM is still starting
// (ADR-0004, decision 20), and that error never reaches the entrypoint.
function turnedAway(stream: string): boolean {
  return stream
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .some((line) => {
      try {
        return (
          (JSON.parse(line.slice(5)) as { type?: unknown }).type === "RUN_ERROR"
        );
      } catch {
        return false;
      }
    });
}

async function warmUpOnce(
  url: string,
  threadId: string,
  fetcher: Fetch,
): Promise<boolean> {
  const response = await fetcher(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      threadId,
      runId: crypto.randomUUID(),
      messages: [],
      forwardedProps: { warmup: true },
    }),
  });
  return response.ok && !turnedAway(await response.text());
}

// Opens the runtime session before the first message, so it doesn't pay the cold start (ADR-0004, decision 20). Each
// new conversation sends one, so a warm-up that fails is sent once more after a pause; an ended sign-in isn't.
export async function warmUp(
  url: string,
  threadId: string,
  fetcher: Fetch,
  pause = 2500,
): Promise<void> {
  try {
    if (await warmUpOnce(url, threadId, fetcher)) return;
  } catch (error) {
    if (error instanceof SignInEndedError) throw error;
  }
  await new Promise((resolve) => setTimeout(resolve, pause));
  await warmUpOnce(url, threadId, fetcher);
}

// A RUN_ERROR's message is never shown: the chat has a fixed sentence per code.
export function problemOf(error: unknown): Problem | "signed_out" {
  if (error instanceof SignInEndedError) return "signed_out";
  const code = (error as { code?: unknown } | null)?.code;
  return isRunErrorCode(code) ? code : "unreachable";
}
