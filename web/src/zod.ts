import { config } from "zod";

// Zod compiles its parsers with new Function unless told not to, and the site's content security policy refuses that
// (ADR-0007). Its first try would be blocked and logged, so it runs before any schema is built.
config({ jitless: true });
