/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#FDFCF7", // Littlebird warm cream
        surface: {
          50: "#FFFFFF",
          100: "#F5F3E9", // Slightly darker cream for cards
          200: "#EAE6D5",
          300: "#D5D0BC",
        },
        brand: {
          50: "#F0F5ED", // Soft green tint
          100: "#DDF0D6",
          500: "#2B5336", // Deep forest green
          600: "#22422B",
          700: "#1A3120",
        },
        accent: {
          emerald: "#10B981",
          amber: "#F59E0B",
          rose: "#E11D48",
          blue: "#1D4ED8",
        },
        dark: {
          DEFAULT: "#1B1A17", // Warm charcoal
          100: "#2D2C27",
          200: "#44433C",
        }
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "Inter", "system-ui", "sans-serif"],
        serif: ["Playfair Display", "Georgia", "serif"],
        mono: ["var(--font-geist-mono)", "JetBrains Mono", "monospace"],
      },
      boxShadow: {
        card: "0 4px 20px rgba(0, 0, 0, 0.05)",
        floating: "0 12px 40px rgba(0, 0, 0, 0.08)",
      },
      animation: {
        "fade-in-up": "fadeInUp 0.8s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "float": "float 6s ease-in-out infinite",
      },
      keyframes: {
        fadeInUp: {
          "0%": { opacity: 0, transform: "translateY(20px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-10px)" },
        }
      }
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};
