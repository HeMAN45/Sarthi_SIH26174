import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
// Bundled, never fetched from a CDN: a font request is a network call, and the
// product's claim is that it runs with none (invariant #1).
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import App from "./App";
import { applyTheme, savedTheme } from "./lib/theme";
import "./styles/theme.css";

// Before the first paint, so a saved theme never flashes the default.
applyTheme(savedTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);
