/**
 * Standalone Node.js HTTP shim for Antigravity's `sidecar_sdk`.
 * Allows `sidecar_ui/main.mjs` to run directly in any standard Node.js environment.
 */
import http from 'node:http';
import { URL } from 'node:url';

export class Response {
  constructor(body, options = {}) {
    this.body = body;
    this.status = options.status || 200;
    this.contentType = options.contentType || 'text/plain; charset=utf-8';
    this.headers = options.headers || {};
  }
}

export class SidecarApp {
  constructor() {
    this.config = {
      conversationId:
        process.env.SWARM_CONVERSATION_ID ||
        '00000000-0000-0000-0000-000000000000',
    };
    this.routes = new Map();
  }

  page(route, handler) {
    this.routes.set(`GET:${route}`, async (data) => {
      const html = await handler(data);
      return new Response(html, { contentType: 'text/html; charset=utf-8' });
    });
  }

  api(route, handler, method = 'GET') {
    this.routes.set(`${method.toUpperCase()}:${route}`, handler);
  }

  run(port = Number(process.env.PORT || 5388)) {
    const server = http.createServer(async (req, res) => {
      try {
        const parsedUrl = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
        const key = `${(req.method || 'GET').toUpperCase()}:${parsedUrl.pathname}`;
        const handler = this.routes.get(key);

        if (!handler) {
          res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
          res.end('Not found');
          return;
        }

        const data = Object.fromEntries(parsedUrl.searchParams.entries());
        const out = await handler(data);

        if (out instanceof Response) {
          res.writeHead(out.status, {
            'Content-Type': out.contentType,
            ...out.headers,
          });
          res.end(out.body);
        } else {
          res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
          res.end(JSON.stringify(out));
        }
      } catch (err) {
        res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify({ error: String(err?.message || err) }));
      }
    });

    const host = process.env.HOST || '127.0.0.1';
    server.listen(port, host, () => {
      console.log(`[swarm_board] Live UI listening at http://${host}:${port}/`);
    });
    return server;
  }
}
