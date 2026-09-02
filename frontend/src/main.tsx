import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";
import { useVoiceSession } from "./useVoiceSession";

const token = new URLSearchParams(window.location.search).get("token") ?? "";
const url = `ws://127.0.0.1:8765/v1/voice?token=${encodeURIComponent(token)}`;

function VoiceClient() {
  return <App controller={useVoiceSession({ url })} />;
}

createRoot(document.getElementById("root")!).render(<VoiceClient />);
