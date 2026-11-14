import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import apiClient, { API_BASE_URL, tokenStore } from "../api/client";
import { Checkbox, Icon, Notice, PageHeader, Segmented } from "../components/ui";

// Admin only: every gate camera, live, laid out like a CCTV monitor wall -
// one row per gate, its entrance camera then its exit camera (CAM 01, CAM 02,
// ...). The gates named in Settings › Camera ("Cameras page: Gate 1 / Gate 2")
// always have their slots; another gate that reports gets a row below. Nothing
// is recorded. The gates send their picture only while this page is open (and
// visible) - see backend logs/camera_monitor.py and entry-agent/monitor_stream.py.

const LIST_REFRESH_MS = 10000;
const RECONNECT_AFTER_ERROR_MS = 2000;
const RECONNECT_AFTER_END_MS = 200; // the backend ends each stream after a few minutes
const SHOW_BOXES_KEY = "securetap_cameras_show_boxes";
const LAYOUT_KEY = "securetap_cameras_layout";
const LAYOUTS = [
  { value: "quad", label: "2 × 2" },
  { value: "big", label: "1 + 3" },
];
const WALL_BG = "#0B0C0D";

// The gate monitor's box colours (entry-agent/ui.py), from the dashboard palette.
const BOX_COLORS = {
  match: "#1E7B45",
  spoof: "#C62828",
  unknown: "#9A5B00",
  card: "#1D5FA8",
  covered: "#1D5FA8",
  checking: "#4B5157",
  not_enrolled: "#9A5B00", // let in, not on this semester's list
  refused: "#C62828", // refused - not enrolled this semester
};

function readSaved(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Remembering the choice is only a convenience.
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

// One connection for every camera shown. Reconnects by itself; a 401 refreshes
// the sign-in through apiClient first.
function useCameraStream(cameraNames, active, onFrame) {
  const [status, setStatus] = useState({});
  const [connection, setConnection] = useState("connecting");
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;
  const key = cameraNames.join("\n");

  useEffect(() => {
    const cameras = key ? key.split("\n") : [];
    if (!active || cameras.length === 0) {
      setConnection(active ? "idle" : "paused");
      return undefined;
    }
    let stopped = false;
    let controller = null;
    let timer = null;

    const connect = async () => {
      controller = new AbortController();
      const query = cameras.map((camera) => `gate=${encodeURIComponent(camera)}`).join("&");
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

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

// A slot with no picture: grey static, like a CCTV channel with no signal.
// Drawn small and stretched (pixelated), about 12 times a second.
function NoSignal({ title, detail, compact }) {
  const canvasRef = useRef(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext("2d");
    const image = ctx.createImageData(canvas.width, canvas.height);
    let frame = null;
    let last = 0;
    const draw = (time) => {
      frame = requestAnimationFrame(draw);
      if (time - last < 80) return;
      last = time;
      const data = image.data;
      for (let i = 0; i < data.length; i += 4) {
        const v = 40 + Math.random() * 110;
        data[i] = v;
        data[i + 1] = v;
        data[i + 2] = v;
        data[i + 3] = 255;
      }
      ctx.putImageData(image, 0, 0);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);
  return (
    <div className="absolute inset-0">
      <canvas ref={canvasRef} width={160} height={90} className="h-full w-full [image-rendering:pixelated]" />
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-s1 px-s3 text-center">
        <span
          className={`bg-black/80 px-s3 py-s1 font-mono font-semibold tracking-[0.2em] text-white ${
            compact ? "text-xs" : "text-lg"
          }`}
        >
          {title}
        </span>
        {detail && !compact && <span className="bg-black/70 px-s2 font-mono text-xs text-ink-400">{detail}</span>}
      </div>
    </div>
  );
}

// One CCTV channel: the live picture with the gate's face boxes, the camera
// number and name over its top-left corner, its state over the top-right -
// or NO SIGNAL static while there's no picture.
function CctvSlot({ slot, number, gate, live, register, compact = false, fill = false, onClick, title }) {
  const slotRef = useRef(null);
  const canvasRef = useRef(null);
  const [hasPicture, setHasPicture] = useState(false);

  useEffect(
    () =>
      register(slot.lane, {
        canvas: () => canvasRef.current,
        shown: () => setHasPicture(true),
      }),
    [slot.lane, register]
  );

  const fullscreen = () => {
    const element = slotRef.current;
    if (!element) return;
    if (document.fullscreenElement === element) document.exitFullscreen?.();
    else element.requestFullscreen?.();
  };

  const isLive = Boolean(live?.live) && hasPicture;
  const side = slot.direction === "exit" ? "EXIT" : "ENTRANCE";
  const camera = `CAM ${String(number).padStart(2, "0")}`;
  let state;
  if (isLive) {
    state = (
      <span className="flex items-center gap-s1 text-white">
        <span className="h-2 w-2 animate-pulse rounded-full bg-[#E53935]" />
        LIVE{live.fps && !compact ? <span className="text-ink-400"> {Math.round(live.fps)} FPS</span> : null}
      </span>
    );
  } else if (slot.online) {
    state = <span className="text-ink-400">CONNECTING</span>;
  } else {
    state = <span className="text-[#E8A33D]">OFFLINE</span>;
  }
  const detail = slot.online
    ? "The gate monitor starts sending within a second or two."
    : slot.reported
      ? `Gate monitor not open · last seen ${timeAgo(slot.last_seen)}`
      : slot.direction === "exit"
        ? "No exit camera reported - choose one in that gate monitor's Cameras & card readers."
        : "This gate's monitor hasn't reported yet.";

  return (
    <div
      ref={slotRef}
      onClick={onClick}
      onDoubleClick={fullscreen}
      title={title}
      className={`group relative min-h-0 overflow-hidden border border-[#24272B] bg-black ${
        fill ? "h-full w-full" : "aspect-video w-full"
      } ${onClick ? "cursor-pointer" : ""}`}
    >
      <canvas
        ref={canvasRef}
        className={`absolute inset-0 h-full w-full object-contain ${isLive ? "" : "invisible"}`}
      />
      {!isLive && <NoSignal title={slot.online ? "CONNECTING" : "NO SIGNAL"} detail={detail} compact={compact} />}
      <div
        className={`pointer-events-none absolute left-0 right-0 top-0 flex items-start justify-between gap-s2 font-mono font-semibold uppercase ${
          compact ? "p-s1 text-[10px]" : "p-s2 text-xs"
        }`}
      >
        <span className="min-w-0 truncate bg-black/70 px-s1 text-white">
          <span className="text-brass">{camera}</span> · {gate} · {side}
        </span>
        <span className="flex-none bg-black/70 px-s1">{state}</span>
      </div>
      {!compact && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            fullscreen();
          }}
          className="absolute bottom-s2 right-s2 hidden rounded-sm bg-black/70 p-s1 text-white group-hover:block"
          title="Full screen (or double-click)"
        >
          <Icon name="corners-out" bold size={16} />
        </button>
      )}
    </div>
  );
}

export default function GateCameras() {
  const [rows, setRows] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState("");
  const [settings, setSettings] = useState(null);
  const [showBoxes, setShowBoxes] = useState(() => readSaved(SHOW_BOXES_KEY, "on") !== "off");
  const [layout, setLayout] = useState(() => (readSaved(LAYOUT_KEY, "quad") === "big" ? "big" : "quad"));
  const [bigLane, setBigLane] = useState(null);
  const [visible, setVisible] = useState(() => !document.hidden);
  const [wall, setWall] = useState(false);
  const wallRef = useRef(null);
  const now = useClock();

  // lane -> {canvas(), shown()} of its slot; lane -> newest picture and faces.
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
        setRows(data.rows || []);
        setSettings({ fps: data.fps, width: data.width });
        setListError("");
      } catch {
        if (!cancelled) setListError("Couldn't load the list of cameras — retrying…");
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

  // Wall mode is the browser's full screen on the wall itself (Esc leaves it).
  useEffect(() => {
    const onChange = () => setWall(document.fullscreenElement === wallRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => {
    save(SHOW_BOXES_KEY, showBoxes ? "on" : "off");
    for (const [lane, picture] of latest.current) {
      drawFrame(tiles.current.get(lane)?.canvas(), picture.bitmap, picture.faces, showBoxes);
    }
  }, [showBoxes]);

  useEffect(() => save(LAYOUT_KEY, layout), [layout]);

  // Free the decoded pictures when leaving the page.
  useEffect(
    () => () => {
      for (const picture of latest.current.values()) picture.bitmap?.close?.();
      latest.current.clear();
    },
    []
  );

  const register = useCallback((lane, tile) => {
    tiles.current.set(lane, tile);
    const picture = latest.current.get(lane);
    if (picture) {
      drawFrame(tile.canvas(), picture.bitmap, picture.faces, showBoxesRef.current);
      tile.shown();
    }
    return () => {
      if (tiles.current.get(lane) === tile) tiles.current.delete(lane);
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

  // Every channel in order - CAM 01 is gate 1's entrance, CAM 02 its exit, ...
  const channels = rows.flatMap((row) => row.slots.map((slot) => ({ slot, gate: row.gate })));
  channels.forEach((channel, index) => {
    channel.number = index + 1;
  });
  const { status, connection } = useCameraStream(
    channels.map((channel) => channel.slot.lane),
    visible,
    onFrame,
  );
  const liveCount = channels.filter((channel) => status[channel.slot.lane]?.live).length;
  const big = channels.find((channel) => channel.slot.lane === bigLane) || channels[0];
  const others = channels.filter((channel) => channel !== big);

  const toggleWall = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wallRef.current?.requestFullscreen?.();
  };

  const connectionText = {
    live: `${liveCount} of ${channels.length} cameras live`,
    connecting: "Connecting…",
    retrying: "Reconnecting…",
    paused: "Paused while this tab is hidden",
    idle: "No cameras",
  }[connection];

  const slotProps = (channel) => ({
    slot: channel.slot,
    number: channel.number,
    gate: channel.gate,
    live: status[channel.slot.lane],
    register,
  });

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
        <Segmented options={LAYOUTS} value={layout} onChange={setLayout} />
        <Checkbox checked={showBoxes} onChange={(event) => setShowBoxes(event.target.checked)} label="Face boxes" />
        <button type="button" onClick={toggleWall} className="btn-secondary">
          <Icon name="frame-corners" size={16} />
          Wall mode
        </button>
      </PageHeader>

      {listError && (
        <Notice tone="caution" className="mt-s5">
          <span className="font-bold">{listError}</span>
        </Notice>
      )}

      {loaded && channels.length === 0 && !listError ? (
        <div className="card mt-s6 flex flex-col justify-center gap-s4 p-s7">
          <Icon name="video-camera" size={44} className="text-ink-600" />
          <span className="font-display stretch-semi text-[28px] font-extrabold leading-tight">No gates set</span>
          <span className="max-w-[520px] text-base leading-normal text-ink-600">
            Name EVSU&apos;s gates in <Link to="/settings#camera" className="font-bold text-maroon underline">Settings › Camera</Link>{" "}
            (Cameras page: Gate 1 and Gate 2) - each gets an entrance and an exit camera here.
          </span>
        </div>
      ) : (
        <div
          ref={wallRef}
          className={`mt-s5 flex flex-col gap-1 rounded-md p-1 ${wall ? "h-screen rounded-none p-2" : ""}`}
          style={{ background: WALL_BG }}
        >
          {/* The DVR strip along the top: what this is, how many are live, the time. */}
          <div className="flex flex-none items-center justify-between gap-s3 px-s2 py-s1 font-mono text-xs font-semibold uppercase text-ink-400">
            <span>
              <span className="text-brass">EVSU SecureTap</span> · CCTV · {liveCount}/{channels.length} live
            </span>
            {wall && <span className="hidden sm:inline">Esc to leave wall mode · double-click a camera for full screen</span>}
            <span className="text-white">
              {now.toLocaleDateString("en-US", { year: "numeric", month: "2-digit", day: "2-digit" })}{" "}
              {now.toLocaleTimeString("en-US", { hour12: false })}
            </span>
          </div>

          {layout === "quad" ? (
            <div
              className={`grid min-h-0 grid-cols-1 gap-1 sm:grid-cols-2 ${wall ? "flex-1 auto-rows-fr" : ""}`}
            >
              {channels.map((channel) => (
                <CctvSlot key={channel.slot.lane} {...slotProps(channel)} fill={wall} />
              ))}
            </div>
          ) : (
            <div
              className={`grid min-h-0 grid-cols-1 gap-1 md:grid-cols-[minmax(0,3fr)_minmax(0,1fr)] ${
                wall ? "flex-1" : ""
              }`}
            >
              {big && <CctvSlot key={big.slot.lane} {...slotProps(big)} fill={wall} />}
              {/* As tall as the big camera (h-0 + min-h-full in its grid row), scrolling past 3. */}
              <div className="grid min-h-0 grid-cols-3 gap-1 md:flex md:h-0 md:min-h-full md:flex-col md:overflow-y-auto">
                {others.map((channel) => (
                  <CctvSlot
                    key={channel.slot.lane}
                    {...slotProps(channel)}
                    compact
                    onClick={() => setBigLane(channel.slot.lane)}
                    title="Show this camera large"
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {settings && channels.length > 0 && (
        <span className="mt-s4 text-sm text-ink-600">
          Gates and their order:{" "}
          <Link to="/settings#camera" className="font-bold text-maroon underline">
            Settings › Camera
          </Link>{" "}
          (Cameras page: Gate 1 / Gate 2); another gate that reports gets its own row. Each camera sends up to{" "}
          {settings.fps} pictures a second, {settings.width} px wide, only while this page is open. Boxes show the
          gate&apos;s own face checks, a moment behind the picture.
        </span>
      )}
    </div>
  );
}
