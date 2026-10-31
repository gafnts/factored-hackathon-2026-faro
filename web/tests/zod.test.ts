import "../src/zod";

import { expect, test } from "vitest";
import { config } from "zod";

test("zod parses without compiling, which the site's CSP refuses", () => {
  expect(config().jitless).toBe(true);
});
