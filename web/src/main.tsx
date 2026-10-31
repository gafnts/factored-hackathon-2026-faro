import "./zod";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "@fontsource-variable/outfit";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app";
import { installPolicy } from "./trusted-types";

installPolicy();
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App path={window.location.pathname} />
    </StrictMode>,
  );
}
