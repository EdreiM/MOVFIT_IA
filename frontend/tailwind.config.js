/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0A0A0A",
        moss: "#141414",
        leaf: "#E51C24",
        lime: "#FF2D35",
        sand: "#FFFFFF",
        ember: "#FF5C5C",
        panel: "#1A1A1A",
        muted: "#A0A0A0",
      },
      fontFamily: {
        display: ['"Syne"', "sans-serif"],
        body: ['"DM Sans"', "sans-serif"],
      },
      backgroundImage: {
        mesh: "radial-gradient(ellipse at 15% 0%, rgba(229,28,36,0.22), transparent 45%), radial-gradient(ellipse at 90% 10%, rgba(229,28,36,0.10), transparent 40%), linear-gradient(165deg, #0A0A0A 0%, #121212 50%, #0A0A0A 100%)",
      },
      boxShadow: {
        brand: "0 0 24px rgba(229, 28, 36, 0.35)",
      },
    },
  },
  plugins: [],
};
