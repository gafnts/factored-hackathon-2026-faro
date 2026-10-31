import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv2020 } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const contracts = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../src/faro/contracts",
);

function read(path: string): unknown {
  return JSON.parse(readFileSync(resolve(contracts, path), "utf8"));
}

const schema = read("chat.schema.json") as { $id: string };
const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
ajv.addSchema(schema);

// Validates a value against one of the chat contract's definitions.
export function errors(definition: string, value: unknown): string[] {
  const validate = ajv.getSchema(`${schema.$id}#/$defs/${definition}`);
  if (!validate) throw new Error(`the chat contract defines no ${definition}`);
  return validate(value)
    ? []
    : (validate.errors ?? []).map(
        (e) => `${e.instancePath} ${e.message ?? ""}`,
      );
}

export function example<T>(name: string, contract = "chat"): T[] {
  return read(`examples/${contract}.${name}.json`) as T[];
}

export interface Event {
  type: string;
  [key: string]: unknown;
}

// A response the Runtime could send: the events as server-sent events.
export function sse(events: readonly Event[], status = 200): Response {
  const body = events
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("");
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}
