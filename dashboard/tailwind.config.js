/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      // The whole palette is these 17 tokens (see docs/design-brief.md) -
      // nothing else. Status colors always travel with an icon and a word,
      // never color alone.
      colors: {
        ink: {
          DEFAULT: "#121416",
          950: "#121416",
          600: "#4B5157",
          400: "#8A9097",
        },
        line: "#D9DCDF",
        canvas: "#EEF0F2",
        surface: "#FFFFFF",
        maroon: {
          DEFAULT: "#7B1113",
          deep: "#4A0A0C",
        },
        // Pulled from the university seal's torch flame. Structural accent
        // only (active nav bar, dividers) - fails contrast as text on white.
        brass: "#C89B3C",
        verified: { DEFAULT: "#1E7B45", tint: "#E3F2E9" },
        caution: { DEFAULT: "#9A5B00", tint: "#FBEFD9" },
        danger: { DEFAULT: "#C62828", tint: "#FBE4E4" },
        prompt: { DEFAULT: "#1D5FA8", tint: "#E2ECF7" },
      },
      fontFamily: {
        // Archivo carries display type through its width axis (condensed
        // eyebrows, semi-expanded titles, expanded hero numbers); Atkinson
        // Hyperlegible Next is the body face; Plex Mono is for data only.
        display: ['"Archivo"', "sans-serif"],
        sans: ['"Atkinson Hyperlegible Next"', "sans-serif"],
        mono: ['"IBM Plex Mono"', "monospace"],
      },
      // Asymmetric spacing rhythm - 4 / 6 / 10 / 16 / 26 / 42 / 68 / 110.
      spacing: {
        s1: "4px",
        s2: "6px",
        s3: "10px",
        s4: "16px",
        s5: "26px",
        s6: "42px",
        s7: "68px",
        s8: "110px",
      },
      borderRadius: {
        sm: "3px",
        md: "8px",
        lg: "14px",
      },
    },
  },
  plugins: [
    ({ addUtilities }) => {
      addUtilities({
        ".stretch-condensed": { "font-stretch": "75%" },
        ".stretch-semi": { "font-stretch": "112.5%" },
        ".stretch-wide": { "font-stretch": "125%" },
      });
    },
  ],
}
