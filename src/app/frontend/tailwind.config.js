export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'db-bg': '#ffffff',
        'db-card': '#f5f5f5',
        'db-border': '#e0e0e0',
        'db-text': '#1f1f1f',
        'db-muted': '#666666',
        'db-accent': '#0066cc',
        'db-red': '#cc0000',
        'db-green': '#00cc00',
      }
    }
  },
  plugins: []
}
