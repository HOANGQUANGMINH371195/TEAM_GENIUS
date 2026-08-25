/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Render's Docker image copies `.next/standalone`, whereas Vercel manages
  // its own Next output. Enabling standalone on Vercel 16.x makes its build
  // finalizer look for a tracing file that Turbopack no longer emits.
  ...(process.env.VERCEL ? {} : { output: "standalone" }),
};

export default nextConfig;
