import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { ToastProvider } from "./components/Toast";
import { ConfigProvider, StatusProvider } from "./app/context";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <StatusProvider>
          <ConfigProvider>
            <App />
          </ConfigProvider>
        </StatusProvider>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>
);
