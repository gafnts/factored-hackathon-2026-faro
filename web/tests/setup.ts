import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom lacks what assistant-ui's viewport measures and scrolls with.
globalThis.ResizeObserver = class {
  observe = () => undefined;
  unobserve = () => undefined;
  disconnect = () => undefined;
};
Element.prototype.scrollTo = () => undefined;
Element.prototype.scrollIntoView = () => undefined;
// Nor does it open or close a dialog.
HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
  this.open = true;
};
HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
  this.open = false;
};
// Nor does it load fonts.
Object.defineProperty(document, "fonts", {
  value: Object.assign(new EventTarget(), { load: () => Promise.resolve([]) }),
});

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});
