import { readdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { compile } from "json-schema-to-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(here, "../../src/faro");
const contracts = resolve(source, "contracts");

export interface Contract {
  schema: string;
  name: string;
  types: string;
}

export const CONTRACTS: readonly Contract[] = [
  {
    schema: "chat.schema.json",
    name: "Chat",
    types: resolve(here, "../src/contracts/chat.ts"),
  },
  {
    schema: "console.schema.json",
    name: "Console",
    types: resolve(here, "../src/contracts/console.ts"),
  },
];

// The words a reply states recorded values in, which the agent's code and the oracle read too.
export const REPLY_WORDS = {
  source: "reply-words.json",
  words: resolve(here, "../src/contracts/words.ts"),
};

// A contract refers to another, or to the handoff schema, by its $id.
async function schemasById(): Promise<Map<string, string>> {
  const folders = [contracts, resolve(source, "policy")];
  const byId = new Map<string, string>();
  for (const folder of folders) {
    for (const file of await readdir(folder)) {
      if (!file.endsWith(".schema.json")) continue;
      const text = await readFile(resolve(folder, file), "utf8");
      byId.set((JSON.parse(text) as { $id: string }).$id, text);
    }
  }
  return byId;
}

export async function generate(contract: Contract): Promise<string> {
  const byId = await schemasById();
  const schema = JSON.parse(
    await readFile(resolve(contracts, contract.schema), "utf8"),
  ) as Parameters<typeof compile>[0];
  return compile(schema, contract.name, {
    unreachableDefinitions: true,
    additionalProperties: false,
    cwd: contracts,
    $refOptions: {
      resolve: {
        urn: {
          order: 1,
          canRead: /^urn:banking-agent:/,
          read: ({ url }: { url: string }) => {
            const text = byId.get(url.split("#")[0] ?? url);
            if (text === undefined) throw new Error(`no schema has $id ${url}`);
            return text;
          },
        },
      },
    },
    bannerComment: `// Generated from src/faro/contracts/${contract.schema} by pnpm contracts.`,
  });
}

export async function generateWords(): Promise<string> {
  const parsed = JSON.parse(
    await readFile(resolve(contracts, REPLY_WORDS.source), "utf8"),
  ) as Record<string, unknown>;
  const words = Object.fromEntries(
    Object.entries(parsed).filter(([key]) => key !== "$comment"),
  );
  return [
    `// Generated from src/faro/contracts/${REPLY_WORDS.source} by pnpm contracts.`,
    "",
    `export const WORDS = ${JSON.stringify(words, null, 2)} as const;`,
    "",
  ].join("\n");
}

if (import.meta.main) {
  for (const contract of CONTRACTS) {
    await writeFile(contract.types, await generate(contract));
  }
  await writeFile(REPLY_WORDS.words, await generateWords());
}
