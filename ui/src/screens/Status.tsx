import { useEffect, useRef, useState } from "react";
import type {
  ActionEvent,
  CandidateEvent,
  MetricsSnapshotEvent,
  StatusEvent,
} from "../lib/types";
import type { AirControlClient, ConnectionState } from "../lib/ws";

type ScreenClient = Pick<
  AirControlClient,
  "on" | "onState" | "send" | "request"
>;

export interface StatusProps {
  client: ScreenClient;
  connectionState: ConnectionState;
}

interface FeedItem {
  id: number;
  description: string;
  kind: string;
  dataKind: string;
  confidence: number;
}

const METRICS_POLL_MS = 5_000;
const MAX_FEED_ITEMS = 50;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}

function metricsValue(value: number, suffix = ""): string {
  return `${value.toFixed(2)}${suffix}`;
}

export function Status({ client, connectionState }: StatusProps) {
  const [status, setStatus] = useState<StatusEvent | null>(null);
  const [metrics, setMetrics] = useState<MetricsSnapshotEvent | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const nextFeedId = useRef(1);

  useEffect(() => {
    if (connectionState !== "open") {
      setStatus(null);
      return;
    }

    const addFeedItem = (
      event: ActionEvent | CandidateEvent,
      description: string,
      kind: string,
      dataKind: string,
    ) => {
      const item: FeedItem = {
        id: nextFeedId.current,
        description,
        kind,
        dataKind,
        confidence: event.confidence,
      };
      nextFeedId.current += 1;
      setFeed((current) => [item, ...current].slice(0, MAX_FEED_ITEMS));
    };

    const unsubscribeStatus = client.on("status", (event) => {
      if (event.type === "status") {
        setStatus(event);
      }
    });
    const unsubscribeAction = client.on("action", (event) => {
      if (event.type === "action") {
        addFeedItem(event, event.description, event.kind, event.kind);
      }
    });
    const unsubscribeCandidate = client.on("candidate", (event) => {
      if (event.type === "candidate") {
        const description =
          event.gate === "abstain"
            ? `Abstained — ${event.reason}`
            : `Candidate passed — ${event.reason}`;
        addFeedItem(event, description, event.gate, event.gate);
      }
    });
    client.send("get_status");

    return () => {
      unsubscribeStatus();
      unsubscribeAction();
      unsubscribeCandidate();
    };
  }, [client, connectionState]);

  useEffect(() => {
    if (connectionState !== "open") {
      return;
    }

    let active = true;
    setMetrics(null);
    setMetricsError(null);
    setMetricsLoading(true);
    const loadMetrics = async () => {
      try {
        const event = await client.request("get_metrics");
        if (event.type !== "metrics_snapshot") {
          throw new Error(`Expected metrics_snapshot, received ${event.type}`);
        }
        if (active) {
          setMetrics(event);
          setMetricsError(null);
        }
      } catch (error) {
        if (active) {
          setMetricsError(errorMessage(error));
        }
      } finally {
        if (active) {
          setMetricsLoading(false);
        }
      }
    };

    void loadMetrics();
    const pollId = window.setInterval(() => {
      void loadMetrics();
    }, METRICS_POLL_MS);

    return () => {
      active = false;
      window.clearInterval(pollId);
    };
  }, [client, connectionState]);

  if (connectionState !== "open") {
    return (
      <>
        <header>
          <h1>Status</h1>
          <p>Live daemon state, decisions, and reliability measurements.</p>
        </header>
        <div className="empty" role="status">
          <strong>Daemon not connected</strong> — start AirControl with app.cmd
        </div>
      </>
    );
  }

  const armed = status?.armed ?? false;

  return (
    <>
      <header>
        <h1>Status</h1>
        <p>Live daemon state, decisions, and reliability measurements.</p>
      </header>

      <section
        className="armed-banner"
        data-armed={armed}
        aria-live="polite"
        aria-busy={status === null}
      >
        <div>
          <div className="state">
            {status === null ? "Loading status…" : armed ? "Armed" : "Paused"}
          </div>
          <div className="detail">
            {status?.status_text ?? "Waiting for the daemon's first status event."}
            {status !== null && (
              <>
                {" · Hold "}
                <span className="mono">{status.hold_progress.toFixed(2)}</span>
              </>
            )}
          </div>
        </div>
        <div className="actions">
          <button
            className="btn primary"
            type="button"
            onClick={() => client.send("toggle_arm")}
          >
            {armed ? "Pause" : "Arm"}
          </button>
          <button
            className="btn"
            type="button"
            onClick={() => client.send("undo")}
          >
            Undo
          </button>
        </div>
      </section>

      {metricsLoading && metrics === null && (
        <div className="empty" role="status">
          Loading reliability metrics…
        </div>
      )}
      {metricsError !== null && (
        <div className="empty" role="alert">
          <strong>Could not load metrics.</strong> {metricsError}
        </div>
      )}
      {metrics !== null && (
        <dl className="metrics-row" aria-label="Reliability metrics">
          <div>
            <dt>False positives / hour</dt>
            <dd className="mono">{metricsValue(metrics.fp_per_hour)}</dd>
          </div>
          <div>
            <dt>Candidates / hour</dt>
            <dd className="mono">
              {metricsValue(metrics.candidates_per_hour)}
            </dd>
          </div>
          <div>
            <dt>Median latency</dt>
            <dd className="mono">
              {metricsValue(metrics.latency_ms_p50, " ms")}
            </dd>
          </div>
          <div>
            <dt>CPU</dt>
            <dd className="mono">{metricsValue(metrics.cpu_pct, "%")}</dd>
          </div>
        </dl>
      )}

      <section className="feed" aria-live="polite">
        <h2>Live decision feed</h2>
        {feed.length === 0 ? (
          <div className="empty">
            <strong>No decisions yet.</strong> Deliberate gestures and abstentions
            will appear here as the daemon evaluates them.
          </div>
        ) : (
          <ol>
            {feed.map((item) => (
              <li key={item.id} data-kind={item.dataKind}>
                <span>{item.description}</span>
                <span>{item.kind}</span>
                <span className="conf mono">{item.confidence.toFixed(2)}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );
}

export default Status;
