function resolvePort(rawPort: string | undefined): number {
  if (rawPort === undefined || rawPort.trim() === "") {
    return 4173;
  }
  if (!/^\d+$/.test(rawPort.trim())) {
    throw new Error("Invalid PORT: must be an integer between 1 and 65535.");
  }
  const parsed = Number(rawPort.trim());
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error("Invalid PORT: must be an integer between 1 and 65535.");
  }
  return parsed;
}

const port = resolvePort(process.env.PORT);

const securityHeaders: Record<string, string> = {
  "Content-Security-Policy":
    "default-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Cache-Control": "no-store",
};

const build = await Bun.build({
  entrypoints: [new URL("./index.html", import.meta.url).pathname],
  target: "browser",
  minify: true,
});
const assets = new Map(build.outputs.map((asset) => [`/${asset.path.split("/").at(-1)}`, asset]));

const server = Bun.serve({
  hostname: "127.0.0.1",
  port,
  development: false,
  fetch(req) {
    if (req.method !== "GET" && req.method !== "HEAD") {
      return new Response("Method Not Allowed", {
        status: 405,
        headers: {
          ...securityHeaders,
          Allow: "GET, HEAD",
          "Content-Type": "text/plain; charset=utf-8",
        },
      });
    }
    const path = new URL(req.url).pathname;
    const asset = assets.get(path === "/" ? "/index.html" : path);
    if (asset) {
      return new Response(req.method === "HEAD" ? null : asset, {
        headers: { ...securityHeaders, "Content-Type": asset.type, "Content-Length": String(asset.size) },
      });
    }
    return new Response("Not Found", {
      status: 404,
      headers: {
        ...securityHeaders,
        "Content-Type": "text/plain; charset=utf-8",
      },
    });
  },
});

console.log(`Moodle local dashboard listening on http://${server.hostname}:${server.port}`);
