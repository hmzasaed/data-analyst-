import { useRef, useState } from "react";
import { getOwnerId, newOwnerId, OWNER_EVENT, setOwnerId } from "../api";
import { navigate, useDismiss, useWindowEvent } from "../hooks";
import { Icon } from "./ui";

/** Shows the workspace (owner) id sent as X-Owner-Id, and lets the user switch it. */
export function OwnerPopover() {
  const [open, setOpen] = useState(false);
  const [owner, setOwner] = useState(getOwnerId);
  const [draft, setDraft] = useState(owner);
  const [copied, setCopied] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useDismiss(open, () => setOpen(false), ref);
  useWindowEvent(OWNER_EVENT, () => setOwner(getOwnerId()));

  const toggle = () => {
    setDraft(owner);
    setCopied(false);
    setOpen((v) => !v);
  };

  const trimmed = draft.trim();
  const invalid = trimmed.length === 0 || trimmed.length > 128;
  const changed = trimmed !== owner;

  const save = () => {
    if (invalid || !changed) return;
    setOwnerId(trimmed);
    setOwner(trimmed);
    setOpen(false);
    navigate("/");
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(owner);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  const short = owner.length > 18 ? `${owner.slice(0, 13)}…${owner.slice(-4)}` : owner;

  return (
    <div className="owner" ref={ref}>
      <button
        type="button"
        className="owner__trigger"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={toggle}
      >
        <Icon name="user" />
        <span className="owner__short">{short}</span>
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Workspace">
          <p className="popover__title">Workspace</p>
          <p className="popover__text">
            Every request is sent with this id, and you only see analyses created under it. Use the same id on
            another browser to see the same work.
          </p>
          <div className="popover__current">
            <code className="owner__full">{owner}</code>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => void copy()}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              save();
            }}
          >
            <label className="field__label" htmlFor="owner-input">
              Switch to workspace id
            </label>
            <input
              id="owner-input"
              className="input"
              value={draft}
              maxLength={128}
              spellCheck={false}
              autoComplete="off"
              onChange={(e) => setDraft(e.target.value)}
            />
            <div className="popover__actions">
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDraft(newOwnerId())}>
                Generate new id
              </button>
              <button type="submit" className="btn btn--primary btn--sm" disabled={invalid || !changed}>
                Switch workspace
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
