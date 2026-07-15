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
      },
    },
  },
  plugins: [],
}

