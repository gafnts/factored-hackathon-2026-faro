import type { ComponentProps, ReactNode } from "react";
import Markdown, { type ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";

import { Cards, cardsIn } from "./cards";
import { Credits, creditsIn } from "./credits";
import { Transactions, transactionsIn } from "./transactions";

// ADR-0007: no raw HTML, no images, and links as plain text, so an injected reply can't make the browser fetch
// anything. react-markdown shows raw HTML as text unless a plugin parses it.
const ALLOWED = [
  "p",
  "br",
  "strong",
  "em",
  "del",
  "ul",
  "ol",
  "li",
  "code",
  "pre",
  "blockquote",
  "h1",
  "h2",
  "h3",
  "h4",
  "hr",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "a",
];

function LinkText({ children }: { children?: ReactNode }) {
  return <span>{children}</span>;
}

// A wide table scrolls on its own, so a phone's page never does.
function ScrollingTable({ children }: { children?: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table>{children}</table>
    </div>
  );
}

// The cards, their credit, and the transactions the agent's code lists, drawn as frames, and any other list as it came;
// the reply's text, which the record keeps and the oracle reads, is the same either way.
function List({ node, children, ...props }: ComponentProps<"ul"> & ExtraProps) {
  const cards = cardsIn(node);
  if (cards) return <Cards cards={cards} />;
  const credits = creditsIn(node);
  if (credits) return <Credits credits={credits} />;
  const statement = transactionsIn(node);
  if (statement) return <Transactions {...statement} />;
  return <ul {...props}>{children}</ul>;
}

// Nothing until the text lands, so the reply waiting for it hides and takes no place in the column.
export function Reply({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="reply space-y-3">
      <Markdown
        remarkPlugins={[remarkGfm]}
        allowedElements={ALLOWED}
        unwrapDisallowed
        components={{ a: LinkText, table: ScrollingTable, ul: List }}
      >
        {text}
      </Markdown>
    </div>
  );
}
