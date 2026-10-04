import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { JobView } from "./components/JobView";
import { Landing } from "./components/Landing";
import { NewAnalysis } from "./components/NewAnalysis";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { useInterval, useRoute } from "./hooks";
import type { Health } from "./types";

export function App() {
  const route = useRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [drawer, setDrawer] = useState(false);

  const checkHealth = useCallback(async () => {
    try {
      setHealth(await api.health());
      setHealthError(false);
    } catch {
      setHealthError(true);
    }
  }, []);

  useEffect(() => {
    void checkHealth();
  }, [checkHealth]);
  useInterval(() => void checkHealth(), healthError ? 5000 : 30000);

  useEffect(() => {
    const titles = { home: "InsightFlow", new: "New analysis · InsightFlow", job: "Analysis · InsightFlow" };
    document.title = titles[route.name];
    setDrawer(false);
  }, [route]);

  return (
    <div className={`shell${drawer ? " drawer-open" : ""}`}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Sidebar route={route} onNavigate={() => setDrawer(false)} />
      <button
        type="button"
        className="scrim"
        aria-label="Close analyses"
        tabIndex={drawer ? 0 : -1}
        onClick={() => setDrawer(false)}
      />
      <div className="main">
        <TopBar health={health} healthError={healthError} onMenu={() => setDrawer(true)} />
        <main id="main" className="content">
          {route.name === "home" && <Landing />}
          {route.name === "new" && <NewAnalysis health={health} />}
          {route.name === "job" && <JobView key={route.id} jobId={route.id} />}
        </main>
      </div>
    </div>
  );
}
