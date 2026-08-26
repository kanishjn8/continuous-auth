import { foundationStatus } from "./protocol";

export function App() {
  const status = foundationStatus();
  return (
    <main>
      <h1>Continuous Authentication</h1>
      <p role="status">System status: {status.protectionStatus}</p>
      <p>Protocol: {status.protocolVersion}</p>
    </main>
  );
}


