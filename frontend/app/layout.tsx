import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "GameCut — Smart Video Editor for Streamers",
  description:
    "Detect dead zones and hype moments in gameplay footage. Export EDL for DaVinci Resolve.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${inter.className} min-h-screen bg-[#0f0f0f] text-white`}>
        <header className="border-b border-white/10 px-6 py-4 flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded bg-indigo-600 flex items-center justify-center text-sm font-bold">
              GC
            </div>
            <span className="font-semibold text-lg tracking-tight">GameCut</span>
          </div>
          <span className="text-white/30 text-sm ml-auto">
            Smart gameplay editor
          </span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
