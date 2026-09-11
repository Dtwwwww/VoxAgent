import { createRoot } from "react-dom/client";

import { App } from "./App";
import { createKnowledgeClient } from "./knowledge/client";
import { createLocalApiClient } from "./localApi";
import "./styles.css";
import { resolveSessionToken } from "./token";
import { useVoiceSession } from "./useVoiceSession";

const token = resolveSessionToken(window.location.search, import.meta.env.VITE_VOXAGENT_TOKEN ?? "");
const url = `ws://127.0.0.1:8765/v1/voice?token=${encodeURIComponent(token)}`;
const knowledgeClient = createKnowledgeClient("http://127.0.0.1:8765", token);
const localApiClient = createLocalApiClient("http://127.0.0.1:8765", token);

function VoiceClient() {
  return <App
    controller={useVoiceSession({ url })}
    knowledgeClient={knowledgeClient}
    localApiClient={localApiClient}
  />;
}

createRoot(document.getElementById("root")!).render(<VoiceClient />);
