import { useEffect, useRef, useState } from "react";
import { Icon } from "./ui";

export default function WebcamCapture({ onCapture }) {
  // The <video> element is always mounted (just hidden via CSS when
  // inactive) so the ref is guaranteed to exist by the time a stream needs
  // attaching - conditionally mounting it caused srcObject to be assigned to
  // a video node that didn't exist yet, leaving the visible element with no
  // source (black screen) once React finished re-rendering.
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [isActive, setIsActive] = useState(false);
  const [error, setError] = useState("");

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setIsActive(false);
  };

  useEffect(() => stopCamera, []);

  const startCamera = async () => {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 480 }, height: { ideal: 360 } },
      });
      streamRef.current = stream;
      setIsActive(true);
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        videoRef.current.play().catch(() => {});
      }
    } catch (err) {
      setError("Could not access the webcam: " + err.message);
    }
  };

  const capture = () => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (blob) {
          onCapture(new File([blob], "capture.jpg", { type: "image/jpeg" }));
        }
        stopCamera();
      },
      "image/jpeg",
      0.92
    );
  };

  return (
    <div className="flex flex-col gap-s2">
      {!isActive && (
        <button type="button" onClick={startCamera} className="btn-secondary btn-sm self-start">
          <Icon name="camera" size={16} />
          Open webcam
        </button>
      )}
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className={`w-full max-w-xs rounded-sm bg-ink ${isActive ? "" : "hidden"}`}
      />
      {isActive && (
        <div className="flex gap-s2">
          <button type="button" onClick={capture} className="btn-primary btn-sm">
            <Icon name="camera" bold size={16} />
            Capture photo
          </button>
          <button type="button" onClick={stopCamera} className="btn-secondary btn-sm">
            Cancel
          </button>
        </div>
      )}
      {error && <p className="text-sm font-bold text-danger">{error}</p>}
    </div>
  );
}
