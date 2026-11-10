import { useCallback, useEffect, useRef, useState } from "react";
import apiClient, { API_BASE_URL, tokenStore } from "../api/client";
import { Checkbox, Icon, Notice, PageHeader } from "../components/ui";

// Admin only: every gate's camera, live, like a CCTV monitor. Nothing is
// recorded. The gates send their picture only while this page is open (and
// visible) - see backend logs/camera_monitor.py and entry-agent/monitor_stream.py.

const LIST_REFRESH_MS = 10000;
const RECONNECT_AFTER_ERROR_MS = 2000;
const RECONNECT_AFTER_END_MS = 200; // the backend ends each stream after a few minutes
const SHOW_BOXES_KEY = "securetap_cameras_show_boxes";

// The gate monitor's box colours (entry-agent/ui.py), from the dashboard palette.
const BOX_COLORS = {
  match: "#1E7B45",
  spoof: "#C62828",
  unknown: "#9A5B00",
  card: "#1D5FA8",
  covered: "#1D5FA8",
  checking: "#4B5157",
};

function readSavedShowBoxes() {
  try {
    return localStorage.getItem(SHOW_BOXES_KEY) !== "off";
  } catch {
    return true;
  }
}

// The stream: messages of [4-byte length][JSON][JPEG bytes when the JSON's size > 0].
async function readMessages(response, onMessage) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = new Uint8Array(0);
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    const joined = new Uint8Array(buffer.length + value.length);
    joined.set(buffer);
    joined.set(value, buffer.length);
    buffer = joined;
    for (;;) {
      if (buffer.length < 4) break;
      const headerLength = new DataView(buffer.buffer, buffer.byteOffset, 4).getUint32(0);
      if (buffer.length < 4 + headerLength) break;
      const header = JSON.parse(decoder.decode(buffer.subarray(4, 4 + headerLength)));
      const size = header.size || 0;
      const end = 4 + headerLength + size;
      if (buffer.length < end) break;
      const payload = size ? buffer.slice(4 + headerLength, end) : null;
      buffer = buffer.slice(end);
      onMessage(header, payload);
    }
  }
}

// One connection for every gate shown. Reconnects by itself; a 401 refreshes
// the sign-in through apiClient first.
function useCameraStream(gateNames, active, onFrame) {
  const [status, setStatus] = useState({});
  const [connection, setConnection] = useState("connecting");
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;
  const key = gateNames.join("\n");

  useEffect(() => {
    const gates = key ? key.split("\n") : [];
    if (!active || gates.length === 0) {
      setConnection(active ? "idle" : "paused");
      return undefined;
    }
    let stopped = false;
    let controller = null;
    let timer = null;

    const connect = async () => {
      controller = new AbortController();
      const query = gates.map((gate) => `gate=${encodeURIComponent(gate)}`).join("&");
      let delay = RECONNECT_AFTER_ERROR_MS;
      try {
        const response = await fetch(`${API_BASE_URL}/gate-cameras/stream?${query}`, {
          headers: { Authorization: `Bearer ${tokenStore.getAccess()}` },
          signal: controller.signal,
          cache: "no-store",
        });
        if (response.status === 401) {
          await apiClient.get("/gate-cameras"); // refreshes the sign-in (or signs out)
          delay = 0;
        } else if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        } else {
          setConnection("live");
          await readMessages(response, (header, payload) => {
            if (header.type === "status") setStatus(header.gates || {});
            else if (header.type === "frame" && payload) onFrameRef.current(header, payload);
          });
          delay = RECONNECT_AFTER_END_MS;
        }
      } catch {
        // Network error, aborted, or the sign-in couldn't be refreshed.
      }
      if (stopped) return;
      if (delay !== RECONNECT_AFTER_END_MS) setConnection("retrying");
      timer = setTimeout(connect, delay);
    };

    setConnection("connecting");
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [key, active]);

  return { status, connection };
}

function drawFrame(canvas, bitmap, faces, showBoxes) {
  if (!canvas || !bitmap) return;
  const { width, height } = bitmap;
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  ctx.drawImage(bitmap, 0, 0);
  if (!showBoxes || !faces?.length) return;
  const line = Math.max(2, Math.round(width / 320));
  const fontSize = Math.max(12, Math.round(width / 52));
  ctx.font = `bold ${fontSize}px "Atkinson Hyperlegible Next", sans-serif`;
  ctx.textBaseline = "middle";
  for (const face of faces) {
    const color = BOX_COLORS[face.kind] || BOX_COLORS.checking;
    const x = face.x * width;
    const y = face.y * height;
    const w = face.w * width;
    const h = face.h * height;
    ctx.lineWidth = line;
    ctx.strokeStyle = color;
    ctx.strokeRect(x, y, w, h);
    const label = face.label || "";
    if (!label) continue;
    const padX = Math.round(fontSize * 0.45);
    const tagH = Math.round(fontSize * 1.6);
    const tagW = Math.min(ctx.measureText(label).width + padX * 2, width);
    const tagX = Math.min(Math.max(0, x - line / 2), width - tagW);
    const tagY = y - tagH >= 0 ? y - tagH : Math.min(y + h, height - tagH);
    ctx.fillStyle = color;
    ctx.fillRect(tagX, tagY, tagW, tagH);
    ctx.fillStyle = "#FFFFFF";
    ctx.fillText(label, tagX + padX, tagY + tagH / 2);
  }
}

function timeAgo(value) {
  if (!value) return "";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 90) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(value).toLocaleDateString();
}

function CameraTile({ gate, live, register, focused, onFocus, onUnfocus }) {
  const tileRef = useRef(null);
  const canvasRef = useRef(null);
  const [hasPicture, setHasPicture] = useState(false);

  useEffect(
    () =>
      register(gate.gate_location, {
        canvas: () => canvasRef.current,
        shown: () => setHasPicture(true),
      }),
    [gate.gate_location, register]
  );

  const fullscreen = () => {
    const element = tileRef.current;
    if (!element) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else element.requestFullscreen?.();
  };

  const isLive = Boolean(live?.live);
  let badge;
  if (isLive) {
    badge = (
      <span className="flex items-center gap-s2 text-sm font-bold text-verified">
        <span className="h-2 w-2 bg-verified" />
        LIVE{live.fps ? <span className="font-mono font-medium text-ink-600">{Math.round(live.fps)} fps</span> : null}
      </span>
    );
  } else if (gate.online) {
    badge = (
      <span className="flex items-center gap-s2 text-sm font-bold text-ink-600">
        <Icon name="circle-notch" bold className="animate-spin" /> Connecting
      </span>
    );
  } else {
    badge = (
      <span className="flex items-center gap-s2 text-sm font-bold text-caution">
        <Icon name="video-camera-slash" bold /> Offline
      </span>
    );
  }

  return (
    <div ref={tileRef} className="card flex min-w-0 flex-col overflow-hidden bg-surface">
      <div className="flex h-11 flex-none items-center gap-s3 border-b border-line px-s4">
        <Icon name="video-camera" bold size={18} className="text-ink-600" />
        <span className="min-w-0 flex-1 truncate font-bold">{gate.gate_location}</span>
        {badge}
        {focused ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={onUnfocus} title="Back to all cameras">
            <Icon name="squares-four" bold /> All
          </button>
        ) : (
          <button type="button" className="btn btn-ghost btn-sm" onClick={onFocus} title="Show this camera alone">
            <Icon name="arrows-out-simple" bold /> Enlarge
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-sm" onClick={fullscreen} title="Full screen">
          <Icon name="corners-out" bold />
        </button>
      </div>
      <div className="relative flex flex-1 items-center justify-center bg-ink">
        <canvas
          ref={canvasRef}
          className={`block h-auto w-full object-contain ${hasPicture ? "" : "aspect-video"} ${
            focused ? "max-h-[calc(100vh-240px)]" : "max-h-full"
          }`}
        />
        {!isLive && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-s2 bg-ink/70 px-s5 text-center text-white">
            <Icon name={gate.online ? "hourglass-medium" : "video-camera-slash"} size={32} />
            <span className="font-bold">{gate.online ? "Waiting for the picture…" : "No signal"}</span>
            <span className="max-w-[360px] text-sm text-ink-400">
              {gate.online
                ? "The gate monitor starts sending within a second or two."
                : `The gate monitor isn't open${gate.last_seen ? ` (last seen ${timeAgo(gate.last_seen)})` : ""}.`}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

export default function GateCameras() {
  const [gates, setGates] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [settings, setSettings] = useState(null);
  const [showBoxes, setShowBoxes] = useState(readSavedShowBoxes);
  const [focusedGate, setFocusedGate] = useState(null);
  const [visible, setVisible] = useState(() => !document.hidden);

  // gate -> {canvas(), shown()} of its tile; gate -> newest picture and faces.
  const tiles = useRef(new Map());
  const latest = useRef(new Map());
  const showBoxesRef = useRef(showBoxes);
  showBoxesRef.current = showBoxes;

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const { data } = await apiClient.get("/gate-cameras");
        if (cancelled) return;
        setGates(data.gates || []);
        setSettings({ fps: data.fps, width: data.width });
        setListError("");
      } catch {
        if (!cancelled) setListError("Couldn't load the list of gates — retrying…");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    };
    load();
    const id = setInterval(load, LIST_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // A hidden tab stops the stream, so the gates stop sending.
  useEffect(() => {
    const onChange = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(SHOW_BOXES_KEY, showBoxes ? "on" : "off");
    } catch {
      // Remembering the choice is only a convenience.
    }
    for (const [gate, picture] of latest.current) {
      drawFrame(tiles.current.get(gate)?.canvas(), picture.bitmap, picture.faces, showBoxes);
    }
  }, [showBoxes]);

  // Free the decoded pictures when leaving the page.
  useEffect(
    () => () => {
      for (const picture of latest.current.values()) picture.bitmap?.close?.();
      latest.current.clear();
    },
    []
  );

  const register = useCallback((gate, tile) => {
    tiles.current.set(gate, tile);
    const picture = latest.current.get(gate);
    if (picture) {
      drawFrame(tile.canvas(), picture.bitmap, picture.faces, showBoxesRef.current);
      tile.shown();
    }
    return () => {
      if (tiles.current.get(gate) === tile) tiles.current.delete(gate);
    };
  }, []);

  const sequence = useRef(0);
  const onFrame = useCallback(async (header, payload) => {
    const order = ++sequence.current;
    let bitmap;
    try {
      bitmap = await createImageBitmap(new Blob([payload], { type: "image/jpeg" }));
    } catch {
      return; // a damaged picture - the next one replaces it
    }
    const previous = latest.current.get(header.gate);
    if (previous && previous.order > order) {
      bitmap.close?.(); // a newer picture was already drawn
      return;
    }
    previous?.bitmap?.close?.();
    latest.current.set(header.gate, { bitmap, faces: header.faces, order });
    const tile = tiles.current.get(header.gate);
    if (tile) {
      drawFrame(tile.canvas(), bitmap, header.faces, showBoxesRef.current);
      tile.shown();
    }
  }, []);

  const shownGates = focusedGate ? gates.filter((gate) => gate.gate_location === focusedGate) : gates;
  const streamGates = shownGates.map((gate) => gate.gate_location);
  const { status, connection } = useCameraStream(streamGates, visible, onFrame);

  useEffect(() => {
    if (focusedGate && loaded && !gates.some((gate) => gate.gate_location === focusedGate)) setFocusedGate(null);
  }, [focusedGate, gates, loaded]);

  // Counted over the gates on screen - an enlarged gate's neighbours aren't streamed.
  const liveCount = shownGates.filter((gate) => status[gate.gate_location]?.live).length;
  const columns =
    focusedGate || shownGates.length <= 1
      ? "grid-cols-1"
      : shownGates.length <= 4
        ? "grid-cols-1 lg:grid-cols-2"
        : "grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3";

  const connectionText = {
    live: focusedGate
      ? liveCount
        ? "Live"
        : "Waiting for the picture…"
      : `${liveCount} of ${gates.length} gate${gates.length === 1 ? "" : "s"} live`,
    connecting: "Connecting…",
    retrying: "Reconnecting…",
    paused: "Paused while this tab is hidden",
    idle: "No gates",
  }[connection];

  return (
    <div className="flex flex-col">
      <PageHeader eyebrow="Admin · live view, not recorded" title="Cameras">
        <div className="flex items-center gap-s3 text-sm text-ink-600">
          <span
            className={`h-2 w-2 ${
              connection === "live" && liveCount > 0 ? "bg-verified" : connection === "retrying" ? "bg-caution" : "bg-ink-400"
            }`}
          />
          <span>{connectionText}</span>
        </div>
        <Checkbox checked={showBoxes} onChange={(event) => setShowBoxes(event.target.checked)} label="Show face boxes" />
      </PageHeader>

      {listError && (
        <Notice tone="caution" className="mt-s5">
          <span className="font-bold">{listError}</span>
        </Notice>
      )}

      {loaded && gates.length === 0 && !listError ? (
        <div className="card mt-s6 flex flex-col justify-center gap-s4 p-s7">
          <Icon name="video-camera" size={44} className="text-ink-600" />
          <span className="font-display stretch-semi text-[28px] font-extrabold leading-tight">No gates yet</span>
          <span className="max-w-[520px] text-base leading-normal text-ink-600">
            A gate appears here once its gate monitor has been opened (on any computer connected to this one). Its
            camera then shows live whenever this page is open.
          </span>
        </div>
      ) : (
        <div className={`mt-s6 grid gap-s5 ${columns}`}>
          {shownGates.map((gate) => (
            <CameraTile
              key={gate.gate_location}
              gate={gate}
              live={status[gate.gate_location]}
              register={register}
              focused={focusedGate === gate.gate_location}
              onFocus={() => setFocusedGate(gate.gate_location)}
              onUnfocus={() => setFocusedGate(null)}
            />
          ))}
        </div>
      )}

      {settings && gates.length > 0 && (
        <span className="mt-s5 text-sm text-ink-600">
          Each gate sends up to {settings.fps} pictures a second, {settings.width} px wide, only while this page is
          open — Settings › Camera › Cameras page smoothness / picture width. Boxes show the gate&apos;s own face
          checks, a moment behind the picture.
        </span>
      )}
    </div>
  );
}
