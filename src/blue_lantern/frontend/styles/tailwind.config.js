/** @type {import('tailwindcss').Config} */
// Mirrors the inline `tailwind.config = {...}` that used to ship with the
// Play CDN script in templates/index.html and templates/login.html.
module.exports = {
  content: [
    "../templates/**/*.html",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "primary": "#bf0100",
        "primary-container": "#ee0000",
        "tertiary-container": "#3e8635",
        "surface": "#fcf9f8",
        "on-surface": "#1c1b1b",
        "on-surface-variant": "#5f3e39",
        "surface-container": "#f0edec",
        "surface-container-high": "#eae7e7",
      },
      fontFamily: {
        display: ["'Red Hat Display'", "system-ui", "sans-serif"],
        body: ["'Red Hat Text'", "system-ui", "sans-serif"],
        mono: ["'Red Hat Mono'", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [
    require("@tailwindcss/forms"),
    require("@tailwindcss/container-queries"),
  ],
};
