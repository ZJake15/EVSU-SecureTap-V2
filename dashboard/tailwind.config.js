/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        maroon: {
          DEFAULT: "#7B1113",
          50: "#fbe9e9",
          100: "#f3c6c7",
          200: "#e39fa1",
          300: "#cf7679",
          400: "#b8494d",
          500: "#7B1113",
          600: "#6b0f11",
          700: "#590c0e",
          800: "#47090b",
          900: "#350608",
        },
        // Pulled from the university seal's torch flame, not invented fresh -
        // muted/desaturated on purpose so it reads as institutional brass
        // (numbered badges, active states) rather than a highlighter accent.
        gold: {
          DEFAULT: "#C89B3C",
          50: "#FBF6E9",
          100: "#F3E4C0",
          200: "#E6CB8B",
          300: "#D9B35C",
          400: "#CBA047",
          500: "#C89B3C",
          600: "#A87D2C",
          700: "#856222",
          800: "#5F461A",
          900: "#3D2D11",
        },
        // Warm neutral scale (replaces Tailwind's default cool gray) so body
        // text and borders don't fight the maroon/gold palette.
        ink: {
          DEFAULT: "#1C1917",
          50: "#F7F5F3",
          100: "#EDE9E5",
          200: "#DDD6CF",
          300: "#C2B8AC",
          400: "#948577",
          500: "#6B5F54",
          600: "#4F463D",
          700: "#3A332C",
          800: "#28221D",
          900: "#1C1917",
        },
        // The page shell's base tone - warm off-white, not stark white or
        // cool gray. Content cards stay bg-white on top of this, so there's
        // real tonal depth between "page" and "card" instead of one flat tone.
        parchment: {
          DEFAULT: "#FAF7F2",
          50: "#FDFCFA",
          100: "#FAF7F2",
          200: "#F3EDE3",
        },
      },
      fontFamily: {
        // Institutional serif for display type (titles, brand, stat numbers) -
        // "state university," not "startup." Inter stays for body/UI text,
        // which needs to hold up at small sizes in dense tables.
        display: ['"Fraunces"', "ui-serif", "Georgia", "serif"],
        sans: ['"Inter"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
}

