import type { ReactNode } from "react";

type Layout = "sign-in" | "chat";

// Placed in cells from the center, where a line runs, so the cleared block and the lit cells sit on the lines. The
// block holds the content; the lit cells keep to the outer columns, or to the rows above and below a phone's
// sign-in, so none sits behind text or glass (the identity guide's Motifs). A phone's empty chat fills its screen,
// so it has none.
const BLOCK: Record<Layout, string> = {
  "sign-in":
    "left-[calc(50%-7*var(--cell)+1px)] top-[calc(50%-7*var(--cell)+1px)] h-[calc(14*var(--cell)-1px)] w-[calc(14*var(--cell)-1px)] sm:left-[calc(50%-5*var(--cell)+1px)] sm:top-[calc(50%-5*var(--cell)+1px)] sm:h-[calc(10*var(--cell)-1px)] sm:w-[calc(10*var(--cell)-1px)]",
  chat: "left-[calc(50%-7*var(--cell)+1px)] top-[calc(50%-8*var(--cell)+1px)] h-[calc(16*var(--cell)-1px)] w-[calc(14*var(--cell)-1px)] sm:left-[calc(50%-8*var(--cell)+1px)] sm:top-[calc(50%-5*var(--cell)+1px)] sm:h-[calc(10*var(--cell)-1px)] sm:w-[calc(16*var(--cell)-1px)]",
};

// Each cell carries its --beat, its turn as the beam passes. Only one set shows at a time, so each starts at zero:
// around the block on a phone (the rows above it, then the same cells turned half around below, one beam's pass),
// the left cells then the right on wider screens.
const ON_PHONES = [
  "sm:hidden [--beat:0ms] left-[calc(50%-4*var(--cell)+1px)] top-[calc(50%-8*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
  "sm:hidden [--beat:180ms] left-[calc(50%-3*var(--cell)+1px)] top-[calc(50%-9*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
  "sm:hidden [--beat:360ms] left-[calc(50%+1*var(--cell)+1px)] top-[calc(50%-9*var(--cell)+1px)] h-[calc(var(--cell)-1px)] w-[calc(2*var(--cell)-1px)]",
  "sm:hidden [--beat:540ms] left-[calc(50%+3*var(--cell)+1px)] top-[calc(50%-8*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
  "sm:hidden [--beat:720ms] left-[calc(50%+3*var(--cell)+1px)] top-[calc(50%+7*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
  "sm:hidden [--beat:900ms] left-[calc(50%+2*var(--cell)+1px)] top-[calc(50%+8*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
  "sm:hidden [--beat:1080ms] left-[calc(50%-3*var(--cell)+1px)] top-[calc(50%+8*var(--cell)+1px)] h-[calc(var(--cell)-1px)] w-[calc(2*var(--cell)-1px)]",
  "sm:hidden [--beat:1260ms] left-[calc(50%-4*var(--cell)+1px)] top-[calc(50%+7*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
];

const LIT: Record<Layout, string[]> = {
  "sign-in": [
    ...ON_PHONES,
    "max-sm:hidden [--beat:0ms] left-[calc(50%-9*var(--cell)+1px)] top-[calc(50%-3*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:180ms] left-[calc(50%-10*var(--cell)+1px)] top-[calc(50%-2*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:360ms] left-[calc(50%-8*var(--cell)+1px)] top-[calc(50%+1px)] h-[calc(var(--cell)-1px)] w-[calc(2*var(--cell)-1px)]",
    "max-sm:hidden [--beat:540ms] left-[calc(50%+6*var(--cell)+1px)] top-[calc(50%-2*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:720ms] left-[calc(50%+8*var(--cell)+1px)] top-[calc(50%-1*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:900ms] left-[calc(50%+9*var(--cell)+1px)] top-[calc(50%+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:1080ms] left-[calc(50%+7*var(--cell)+1px)] top-[calc(50%+1*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
  ],
  chat: [
    "max-sm:hidden [--beat:0ms] left-[calc(50%-9*var(--cell)+1px)] top-[calc(50%-3*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:180ms] left-[calc(50%-10*var(--cell)+1px)] top-[calc(50%-2*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:360ms] left-[calc(50%-10*var(--cell)+1px)] top-[calc(50%+1*var(--cell)+1px)] h-[calc(var(--cell)-1px)] w-[calc(2*var(--cell)-1px)]",
    "max-sm:hidden [--beat:540ms] left-[calc(50%+8*var(--cell)+1px)] top-[calc(50%-3*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:720ms] left-[calc(50%+9*var(--cell)+1px)] top-[calc(50%-2*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:900ms] left-[calc(50%+9*var(--cell)+1px)] top-[calc(50%+1px)] size-[calc(var(--cell)-1px)]",
    "max-sm:hidden [--beat:1080ms] left-[calc(50%+8*var(--cell)+1px)] top-[calc(50%+2*var(--cell)+1px)] size-[calc(var(--cell)-1px)]",
  ],
};

// The banner's grid behind a page's content: hairlines that fade toward the edges, a block cleared for the content,
// and a few lit cells. The lines fade in, the content rises, and the cells come on last, the first beam passing
// with them; then only the beacon moves (the identity guide's Motifs). The hairlines run on under the shell's rail and bar (by --rail and
// --bar), centered on the content all the same; the block and the lit cells keep to the page. The cells are whole
// pixels, and the lines are drawn from a corner whole cells off the center, as the block is placed, so a browser
// rounds the two alike and the lines meet the block on every side.
export function Grid({
  layout,
  children,
}: {
  layout: Layout;
  children: ReactNode;
}) {
  return (
    <div className="relative isolate flex flex-1 flex-col">
      <div
        aria-hidden="true"
        data-grid={layout}
        className="pointer-events-none absolute inset-0 -z-10"
      >
        <div className="absolute -top-(--bar) right-0 bottom-0 -left-(--rail) animate-fade overflow-hidden">
          <div className="absolute top-0 -right-(--rail) -bottom-(--bar) left-0 [mask-image:radial-gradient(ellipse_at_center,black_45%,transparent_85%)]">
            <div className="absolute top-[calc(50%-64*var(--cell))] left-[calc(50%-64*var(--cell))] size-[calc(128*var(--cell))] bg-[linear-gradient(to_right,rgb(255_255_255/0.11)_1px,transparent_1px),linear-gradient(to_bottom,rgb(255_255_255/0.12)_1px,transparent_1px)] bg-size-[var(--cell)_var(--cell)]" />
          </div>
        </div>
        <div className="absolute inset-0 overflow-hidden">
          <div className={`absolute bg-night ${BLOCK[layout]}`} />
          {LIT[layout].map((place) => (
            <div key={place} className={`absolute beacon lit ${place}`} />
          ))}
        </div>
      </div>
      <div className="flex flex-1 animate-arrive flex-col motion-reduce:animate-fade">
        {children}
      </div>
    </div>
  );
}
