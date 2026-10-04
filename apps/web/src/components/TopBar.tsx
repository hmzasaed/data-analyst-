import type { Health } from "../types";
import { OwnerPopover } from "./OwnerPopover";
import { Icon } from "./ui";

interface Props {
  health: Health | null;
  healthError: boolean;
  onMenu: () => void;
}

export function TopBar({ health, healthError, onMenu }: Props) {
  const online = !!health && !healthError;
  const storage = health?.state_backend ?? (health ? "sqlite" : null);
  const mode = health?.execution_mode ?? (health?.inline_execution ? "inline" : health ? "worker" : null);

  return (
    <header className="topbar">
      <button type="button" className="iconbtn topbar__menu" onClick={onMenu} aria-label="Open analyses">
        <Icon name="menu" size={18} />
      </button>

      <div className="health" aria-live="polite">
        <span className={`health__item health__api ${online ? "is-ok" : healthError ? "is-bad" : ""}`}>
          <span className="health__dot" aria-hidden="true" />
          {online ? "API online" : healthError ? "API unreachable" : "Connecting"}
        </span>
        {online && (
          <>
            <span
              className={`health__item ${health.llm_enabled ? "is-on" : "is-off"}`}
              title={
                health.llm_enabled
                  ? "A language model helps plan the analysis and write prose. Numbers are always computed."
                  : "No language model calls. Planning and prose are deterministic."
              }
            >
              Language model {health.llm_enabled ? "on" : "off"}
            </span>
            {storage && (
              <span className="health__item health__extra" title="Where job state is stored">
                Storage: {storage}
              </span>
            )}
            {mode && (
              <span className="health__item health__extra" title="How jobs are executed">
                Runs: {mode}
              </span>
            )}
            {health.environment && health.environment !== "production" && (
              <span className="health__item health__extra">{health.environment}</span>
            )}
          </>
        )}
      </div>

      <OwnerPopover />
    </header>
  );
}
