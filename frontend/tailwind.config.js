/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ['Newsreader', 'Georgia', 'Cambria', 'serif'],
        sans: ['"Plus Jakarta Sans"', 'Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        harvey: {
          dark: '#0D0D11',
          sidebar: '#111116',
          canvas: '#FBFBFB',
          card: '#FFFFFF',
          border: '#EAEAEA',
          muted: '#71717A',
          subtle: '#F4F4F5',
        },
        brand: {
          50: '#F0F7FF',
          100: '#E0EFFF',
          500: '#0284C7',
          800: '#0B3B60',
          900: '#0F172A',
        },
        ink: '#171717',
        body: '#525252',
        muted: '#8A8A8A',
        paper: '#FAFAFA',
        surface: '#FFFFFF',
        hairline: '#E5E5E5',
        accent: '#CA8A04',
        saddle: '#0D0D11',
      }
    },
  },
  plugins: [],
}
