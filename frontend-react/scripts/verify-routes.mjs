// Gate 5 verification: does the BFF serve the SPA for every route?
// Uses a placeholder Clerk key, which is enough for static/SPA serving.
process.env.VITE_CLERK_PUBLISHABLE_KEY ||= "pk_test_placeholder";
process.env.CLERK_SECRET_KEY ||= "sk_test_placeholder";
process.env.BACKEND_API_URL ||= "http://localhost:8000";

const { createApp } = await import("../server/index.js");

const app = createApp();
const server = app.listen(0);
await new Promise((r) => server.once("listening", r));
const base = `http://127.0.0.1:${server.address().port}`;

const routes = [
  "/",
  "/chat",
  "/faq",
  "/schemes",
  "/schemes/pmfby",
  "/services",
  "/services/some-slug",
  "/legal",
  "/legal/some-slug",
  "/library",
  "/grievance",
  "/grievance/status",
  "/grievance/draft/view",
  "/sign-in/foo",
  "/sign-up/foo",
  "/this-route-does-not-exist",
];

for (const path of routes) {
  const res = await fetch(base + path);
  const body = await res.text();
  const spa = body.includes('<div id="root">');
  console.log(
    String(res.status).padEnd(5),
    path.padEnd(30),
    spa ? "serves SPA shell" : "OTHER -> " + body.slice(0, 70).replace(/\n/g, " "),
  );
}

const fav = await fetch(base + "/favicon.ico");
console.log("\nfavicon.ico ->", fav.status, fav.headers.get("content-type"));

const css = await fetch(base + "/assets/" + (await (async () => {
  const fs = await import("node:fs");
  const d = "dist/assets";
  const f = fs.readdirSync(d).find((x) => x.endsWith(".css"));
  return f;
})()));
console.log("hashed css asset ->", css.status, css.headers.get("content-type"));

server.close();
process.exit(0);