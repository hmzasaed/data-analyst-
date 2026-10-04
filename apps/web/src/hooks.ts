import { useEffect, useRef, useState } from "react";

export type Route = { name: "home" } | { name: "new" } | { name: "job"; id: string };

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, "");
  const job = path.match(/^\/jobs\/([^/?#]+)/);
  if (job) return { name: "job", id: decodeURIComponent(job[1]) };
  if (path.startsWith("/new")) return { name: "new" };
  return { name: "home" };
}

export function navigate(path: string): void {
  window.location.hash = path;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

/** Call `fn` every `ms` while `enabled`; the latest `fn` is always used. */
export function useInterval(fn: () => void, ms: number, enabled = true): void {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(() => saved.current(), ms);
    return () => window.clearInterval(id);
  }, [ms, enabled]);
}

/** Re-render periodically so relative times stay fresh. */
export function useNow(ms = 30000): number {
  const [now, setNow] = useState(() => Date.now());
  useInterval(() => setNow(Date.now()), ms);
  return now;
}

export function useWindowEvent(name: string, handler: () => void): void {
  const saved = useRef(handler);
  saved.current = handler;
  useEffect(() => {
    const fn = () => saved.current();
    window.addEventListener(name, fn);
    return () => window.removeEventListener(name, fn);
  }, [name]);
}

export interface Resource<T> {
  data: T | null;
  error: string | null;
  /** The backend answered 409: this output doesn't exist yet. */
  notReady: boolean;
  loading: boolean;
  reload: () => void;
}

/**
 * Fetch a job sub-resource. `key` changes (e.g. when another stage completes) trigger a
 * quiet refetch that keeps the previous data on screen.
 */
export function useResource<T>(fetcher: () => Promise<T>, key: string): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notReady, setNotReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const saved = useRef(fetcher);
  saved.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    saved
      .current()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(null);
        setNotReady(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const status = (e as { status?: number })?.status;
        if (status === 409) {
          setNotReady(true);
          setError(null);
        } else {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key, nonce]);

  return { data, error, notReady, loading, reload: () => setNonce((n) => n + 1) };
}

/** Close a popover on outside click or Escape. */
export function useDismiss(open: boolean, onClose: () => void, ref: React.RefObject<HTMLElement>): void {
  const saved = useRef(onClose);
  saved.current = onClose;
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) saved.current();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") saved.current();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, ref]);
}
