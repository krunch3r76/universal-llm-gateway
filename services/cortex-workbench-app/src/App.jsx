import CortexWorkbench from "@workbench/CortexWorkbench.jsx";

const envToken = import.meta.env.VITE_VORTEX_TOKEN || "";

export default function App() {
  return <CortexWorkbench initialToken={envToken} />;
}
