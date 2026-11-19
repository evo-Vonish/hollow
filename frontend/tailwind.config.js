/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // HOLLOW 纸质设计 tokens(2026-07 重构:纸质/轻盈/低噪音/轻快/阅读)
        bg: {
          0: '#FAF8F3', // 纸面
          1: '#FEFDFB', // 纸卡
          2: '#F2EEE5', // 淡墨(骨架/悬停)
        },
        line: {
          DEFAULT: '#E6E1D6', // 发丝线
          strong: '#D4CDBC',
        },
        ink: {
          0: '#29251F', // 暖墨
          1: '#5C5546', // 次级墨
          2: '#9A917E', // 淡墨
        },
        signal: '#2E7D5B', // 墨绿(品牌/成功)
        cyan: '#3F6E9E',   // 墨蓝(链接)
        amber: '#B45309',  // 赭(警示)
        danger: '#B3392F', // 朱(危险)
        violet: '#7C6BAE',
        'tier-removed': '#9A917E',
        // shadcn/ui legacy tokens (kept so src/components/ui keeps compiling)
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground) / <alpha-value>)",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar-background))",
          foreground: "hsl(var(--sidebar-foreground))",
          primary: "hsl(var(--sidebar-primary))",
          "primary-foreground": "hsl(var(--sidebar-primary-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
          ring: "hsl(var(--sidebar-ring))",
        },
      },
      fontFamily: {
        display: ['"Noto Serif SC"', 'Georgia', '"Songti SC"', 'serif'],
        sans: ['Inter', '"Noto Sans SC"', 'system-ui', 'sans-serif'],
        serif: ['Georgia', '"Noto Serif SC"', '"Songti SC"', 'serif'],
        mono: ['"JetBrains Mono"', '"Noto Sans SC"', 'ui-monospace', 'monospace'],
      },
      maxWidth: {
        content: '1200px',
        wide: '1320px',
      },
      borderRadius: {
        xl: "calc(var(--radius) + 4px)",
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xs: "calc(var(--radius) - 6px)",
      },
      boxShadow: {
        xs: '0 1px 2px 0 rgb(41 37 31 / 0.04)',
        // 轻盈:阴影只用于浮层,且极浅
        hover: '0 6px 24px -6px rgb(41 37 31 / .12)',
      },
      transitionTimingFunction: {
        // design.md §5: cubic-bezier(0.22, 1, 0.36, 1)
        expo: 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "caret-blink": {
          "0%,70%,100%": { opacity: "1" },
          "20%,50%": { opacity: "0" },
        },
        "pulse-ring": {
          "0%": { transform: "scale(1)", opacity: "0.7" },
          "100%": { transform: "scale(2.6)", opacity: "0" },
        },
        "scroll-line": {
          "0%": { transform: "scaleY(0.2)", transformOrigin: "top" },
          "50%": { transform: "scaleY(1)", transformOrigin: "top" },
          "51%": { transformOrigin: "bottom" },
          "100%": { transform: "scaleY(0.2)", transformOrigin: "bottom" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "caret-blink": "caret-blink 1s step-end infinite",
        "pulse-ring": "pulse-ring 2.4s cubic-bezier(0.22, 1, 0.36, 1) infinite",
        "scroll-line": "scroll-line 1.8s cubic-bezier(0.22, 1, 0.36, 1) infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}
