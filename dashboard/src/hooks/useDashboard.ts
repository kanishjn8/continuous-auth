import { useEffect, useReducer, useRef } from "react";

import { ApiFailure, loadDashboard } from "../api/client";
import { dashboardConfig } from "../config";
import { isEnvelope } from "../protocol";
import { dashboardReducer, initialDashboardState } from "../state/dashboard";

export function useDashboard(
  authenticated: boolean,
  onUnauthorized: () => void,
) {
  const [state, dispatch] = useReducer(dashboardReducer, initialDashboardState);
  const cursor = useRef(-1);

  useEffect(() => {
    if (!authenticated) return;
    let stopped = false;
    let reconnectTimer: number | null = null;
    let staleTimer: number | null = null;
    let socket: WebSocket | null = null;
    let reconnectDelay = dashboardConfig.reconnect_initial_ms;

    const resetStaleTimer = () => {
      if (staleTimer !== null) window.clearTimeout(staleTimer);
      staleTimer = window.setTimeout(() => {
        if (!stopped) dispatch({ type: "STALE" });
      }, dashboardConfig.stale_after_ms);
    };

    const connect = () => {
      if (stopped) return;
      dispatch({ type: "CONNECTING" });
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const query = cursor.current >= 0 ? `?cursor=${cursor.current}` : "";
      socket = new WebSocket(
        `${scheme}://${window.location.host}/v1/stream${query}`,
      );
      socket.onopen = () => {
        reconnectDelay = dashboardConfig.reconnect_initial_ms;
        resetStaleTimer();
      };
      socket.onmessage = (event) => {
        try {
          const envelope: unknown = JSON.parse(String(event.data));
          if (!isEnvelope(envelope))
            throw new Error("invalid live-update envelope");
          cursor.current = envelope.stream_seq;
          dispatch({ type: "STREAM", envelope, receivedAt: Date.now() });
          resetStaleTimer();
        } catch {
          dispatch({
            type: "OFFLINE",
            error: "A live update failed contract validation.",
          });
          socket?.close();
        }
      };
      socket.onclose = (event) => {
        if (stopped) return;
        if (event.code === 4401) {
          onUnauthorized();
          return;
        }
        if (event.code === 4403) {
          // The backend closed the stream because enforcement is blocking.
          // Reconnecting would be refused the same way, and retrying in a
          // loop would bury the verification screen under stream errors.
          dispatch({
            type: "OFFLINE",
            error: "Live monitoring is paused until identity is verified.",
          });
          return;
        }
        dispatch({
          type: "OFFLINE",
          error: "Live monitoring is disconnected.",
        });
        reconnectTimer = window.setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(
          reconnectDelay * 2,
          dashboardConfig.reconnect_max_ms,
        );
      };
    };

    loadDashboard()
      .then((value) => {
        if (stopped) return;
        dispatch({
          type: "LOADED",
          value: {
            current: value.state,
            alerts: value.alerts,
            decisions: value.decisions,
            health: value.health,
            metrics: value.metrics,
            profiles: value.profiles,
            updates: value.updates,
          },
        });
        connect();
      })
      .catch((error: unknown) => {
        if (error instanceof ApiFailure && error.status === 401)
          onUnauthorized();
        else
          dispatch({
            type: "OFFLINE",
            error: "Backend state could not be loaded.",
          });
      });

    return () => {
      stopped = true;
      socket?.close();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (staleTimer !== null) window.clearTimeout(staleTimer);
    };
  }, [authenticated, onUnauthorized]);

  return { state, dispatch };
}
