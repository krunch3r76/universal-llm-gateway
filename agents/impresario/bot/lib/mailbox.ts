import { z } from "zod";

/** Bus mailboxes the impresario may address (`to=`). */
export const busMailbox = z.enum([
  "maestro",
  "grok-bot-gc1",
  "web-anthropic",
  "cursor",
  "impresario-sdk",
  "cursor-sdk",
  "grok-bot",
]);

export type BusMailbox = z.infer<typeof busMailbox>;
