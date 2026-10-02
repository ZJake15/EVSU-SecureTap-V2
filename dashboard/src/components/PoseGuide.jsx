// Outline head drawings showing the pose for each guided-enrollment shot.
// Drawn in currentColor so the caller's text color sets the line color.
//
// "Slight left" faces the picture's left, i.e. the way the person turns if
// they copy the drawing like a mirror - the same reading as the "Turn left a
// little" hint. "Slight right" is the same drawing flipped.

const FRONT_OUTLINE = "M34 62 C34 92 44 112 60 116 C76 112 86 92 86 62";
const TURNED_OUTLINE = "M34 60 C33 80 38 100 48 110 C51 113 53 115 55 116 C70 113 84 96 86 62";
// Short hair swept up and to the right, flicking out at the crown.
const HAIR =
  "M33 64 C31 44 38 30 52 25 C60 21 70 20 78 23 C84 20 90 22 93 25 C90 26 88 27 87 29 " +
  "C91 33 93 38 92 43 C90 42 89 41 87.5 41 C88.5 48 88 56 86 64 C84 54 82 48 78 45 " +
  "C70 47 58 46 50 43 C44 46 40 50 37 56 C35 59 34 62 33 64 Z";
const LEFT_EAR = "M34 66 C27 62 25 76 30 84 C32 88 35 88 36 86";
const RIGHT_EAR = "M86 66 C93 62 95 76 90 84 C88 88 85 88 84 86";
const SHOULDERS = "M44 130 Q30 138 10 142 M76 130 Q90 138 110 142";

const FRONT_FEATURES = {
  brows: "M42 62 Q49 58 55 61 M65 61 Q71 58 78 62",
  eyes: "M44 70 Q49 66 54 70 Q49 72 44 70 M66 70 Q71 66 76 70 Q71 72 66 70",
  pupils: [
    [49, 69.4],
    [71, 69.4],
  ],
  nose: "M56.5 77 Q55 82.5 56.5 85.5 Q60 88.5 63.5 85.5 Q65 82.5 63.5 77",
  neck: "M46 112 L44 132 M74 112 L76 132",
};

const MOUTHS = {
  // Relaxed lips, like a passport photo.
  front: "M51 97 Q55 95 60 96.5 Q65 95 69 97 M51 97 Q60 99 69 97 M53 98.5 Q60 103 67 98.5",
  // Closed, flat - no expression at all.
  neutral: "M51 97.5 L69 97.5 M54 100.5 Q60 102 66 100.5",
  // Open, natural smile.
  smile: "M50 95.5 Q60 104.5 70 95.5 Q60 98.5 50 95.5 Z M48.5 94.5 Q49.5 95 50 95.5 M71.5 94.5 Q70.5 95 70 95.5",
  turned: "M45 97 Q49 95.2 53 96.5 Q58 95 62 97 M45 97 Q53 99 62 97 M47 98.5 Q53.5 102.5 60 98.5",
};

function Head({ turned, mouth }) {
  const features = turned
    ? {
        brows: "M37 62 Q43 58.5 48 61 M59 61 Q66 58 73 62",
        eyes: "M39 70 Q43 67 47 70 Q43 71.6 39 70 M59 70 Q64.5 66 70 70 Q64.5 72 59 70",
        pupils: [
          [42.5, 69.4],
          [63.5, 69.4],
        ],
        nose: "M51 72 Q49.5 80 46 86 Q48.5 89 53 87",
        neck: "M46 112 L45 132 M72 110 L76 132",
      }
    : FRONT_FEATURES;

  return (
    <g fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
      <path d={turned ? TURNED_OUTLINE : FRONT_OUTLINE} strokeWidth="1.6" />
      {!turned && <path d={LEFT_EAR} strokeWidth="1.6" />}
      <path d={RIGHT_EAR} strokeWidth="1.6" />
      <path d={HAIR} fill="currentColor" strokeWidth="1.2" />
      <path d={features.neck} strokeWidth="1.6" />
      <path d={SHOULDERS} strokeWidth="1.6" />
      <path d={features.brows} strokeWidth="2.6" />
      <path d={features.eyes} strokeWidth="1.3" />
      {features.pupils.map(([cx, cy]) => (
        <circle key={cx} cx={cx} cy={cy} r="1.7" fill="currentColor" stroke="none" />
      ))}
      <path d={features.nose} strokeWidth="1.3" />
      <path d={MOUTHS[mouth]} strokeWidth="1.3" />
    </g>
  );
}

const POSES = {
  front: { turned: false, mouth: "front", label: "Facing straight at the camera" },
  left: { turned: true, mouth: "turned", label: "Head turned slightly left" },
  right: { turned: true, mouth: "turned", label: "Head turned slightly right", flip: true },
  neutral: { turned: false, mouth: "neutral", label: "Facing the camera, no expression" },
  smile: { turned: false, mouth: "smile", label: "Facing the camera, smiling" },
};

export default function PoseGuide({ pose, className = "" }) {
  const spec = POSES[pose] || POSES.front;
  return (
    <svg viewBox="0 0 120 146" role="img" aria-label={spec.label} className={className}>
      <g transform={spec.flip ? "translate(120 0) scale(-1 1)" : undefined}>
        <Head turned={spec.turned} mouth={spec.mouth} />
      </g>
    </svg>
  );
}
