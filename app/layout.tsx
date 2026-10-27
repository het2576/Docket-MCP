import type { Metadata, Viewport } from "next";
import "@fontsource-variable/schibsted-grotesk";
import "@fontsource-variable/bricolage-grotesque/standard.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Docket",
  description: "Turn meeting transcripts into reviewed, tracker-ready action items."
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#222736" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1117" }
  ]
};

// Applies a saved light/dark choice before first paint so the page never flashes the wrong theme.
const themeScript = `try{var t=localStorage.getItem("docket:theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
