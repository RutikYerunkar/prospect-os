import type { Metadata } from "next";
import { JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Groundwork",
  description: "Evidence-backed, scored prospects with drafted outreach — human-approved.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${jetbrainsMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col bg-zinc-950 text-zinc-100">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-zinc-800 px-4">
          <Link
            href="/plays/new"
            className="flex items-center gap-2 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"
          >
            <span className="h-2 w-2 rounded-sm bg-indigo-400" aria-hidden />
            <span className="text-sm font-semibold tracking-tight">Groundwork</span>
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            <Link
              href="/plays/new"
              className="text-zinc-400 transition-colors hover:text-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"
            >
              New Play
            </Link>
            <Link
              href="/settings"
              className="text-zinc-400 transition-colors hover:text-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-400"
            >
              Settings
            </Link>
          </nav>
        </header>
        <div className="flex flex-1 flex-col">{children}</div>
      </body>
    </html>
  );
}
