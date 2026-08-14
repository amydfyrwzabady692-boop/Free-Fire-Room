/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#070b14",
        panel: "#101827",
        line: "#1f2a3d",
        accent: "#ff7a18",
        good: "#22c55e",
      },
    },
  },
  plugins: [],
};
