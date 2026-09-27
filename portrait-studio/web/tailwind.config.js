/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        ink: { 950: "#0b0f19", 900: "#111827", 800: "#1f2937", 700: "#374151", 600: "#4b5563" },
        accent: { DEFAULT: "#6366f1", hover: "#4f46e5", soft: "#312e81" },
      },
      fontFamily: {
        sans: ['"Hiragino Sans"', '"Noto Sans JP"', '"Yu Gothic"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};
