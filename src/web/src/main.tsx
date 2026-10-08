import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { installLanSessionExpiryMonitor } from "./api/lanSession";
import "./design-system/reset.css";
import "./design-system/tokens.css";
import "./design-system/ornament.css";
import "./index.css";
import "./design-system/dialogs.css";
import "./story-panels.css";
import "./group-actors.css";
import "./simple-chat.css";
import "./mobile-access.css";
import "./appearance/app-shell.css";
import "./appearance/pointer-effects.css";
import "./components/load-state.css";
import "./workspace-layout.css";
import "./appearance/bubbles.css";
import "./components/composer.css";
import "./appearance/styles/celestial-atelier.css";
import "./appearance/fonts.css";
import "./appearance/styles/visual-variants.css";
import "./appearance/workspace-refresh.css";
import "./appearance/buttons/buttons.css";
import "./design-system/picturebook.css";

installLanSessionExpiryMonitor();

window.addEventListener("vite:preloadError", (event) => {
  const preloadEvent = event as Event & { payload?: unknown };
  console.error("页面动态资源加载失败", preloadEvent.payload ?? event);
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
