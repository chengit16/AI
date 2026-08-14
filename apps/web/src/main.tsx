/** Web 应用唯一浏览器入口，按 Token、全局规则、UnoCSS 的顺序装载样式层。 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "@/app/App";
import { AppProviders } from "@/app/AppProviders";
import "@/styles/tokens.css";
import "@/styles/global.css";
import "virtual:uno.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AppProviders>
        <App />
      </AppProviders>
    </BrowserRouter>
  </StrictMode>,
);
