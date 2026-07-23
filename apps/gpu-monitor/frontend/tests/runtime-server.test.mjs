import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const runtimeServerPath = fileURLToPath(new URL('../server.mjs', import.meta.url));

test('production runtime server is committed beside the frontend build', () => {
	assert.equal(existsSync(runtimeServerPath), true);
});

async function loadRuntimeServer() {
	const runtime = await import('../server.mjs');
	assert.equal(typeof runtime.createMonitoringServer, 'function');
	return runtime.createMonitoringServer;
}

async function listen(server) {
	await new Promise((resolve, reject) => {
		server.once('error', reject);
		server.listen(0, '127.0.0.1', () => {
			server.off('error', reject);
			resolve();
		});
	});
	return server.address().port;
}

async function close(server) {
	if (!server.listening) return;
	await new Promise((resolve, reject) => {
		server.close((error) => (error ? reject(error) : resolve()));
	});
}

async function request(port, path) {
	return await new Promise((resolve, reject) => {
		const req = http.get({ host: '127.0.0.1', port, path }, (response) => {
			const chunks = [];
			response.on('data', (chunk) => chunks.push(chunk));
			response.on('end', () => {
				resolve({
					statusCode: response.statusCode,
					headers: response.headers,
					body: Buffer.concat(chunks).toString('utf8')
				});
			});
		});
		req.on('error', reject);
	});
}

test('the production proxy accepts IPv4 and IPv6 loopback targets only', async () => {
	const createMonitoringServer = await loadRuntimeServer();
	const handler = (_req, res) => res.end();
	assert.doesNotThrow(() =>
		createMonitoringServer({ backendTarget: 'http://127.0.0.1:8101', handler })
	);
	assert.doesNotThrow(() =>
		createMonitoringServer({ backendTarget: 'http://[::1]:8101', handler })
	);
	assert.throws(
		() => createMonitoringServer({ backendTarget: 'http://192.0.2.10:8101', handler }),
		/loopback/
	);
});

test('HTTP API requests strip only the /api prefix and preserve the query string', async () => {
	const createMonitoringServer = await loadRuntimeServer();
	let observedUrl = null;
	const backend = http.createServer((req, res) => {
		observedUrl = req.url;
		res.writeHead(200, { 'content-type': 'application/json', 'x-backend': 'gpu-monitor' });
		res.end('{"ok":true}');
	});
	const backendPort = await listen(backend);
	const frontend = createMonitoringServer({
		backendTarget: `http://127.0.0.1:${backendPort}`,
		handler: (_req, res) => {
			res.writeHead(418);
			res.end('unexpected adapter handler');
		}
	});
	const frontendPort = await listen(frontend);

	try {
		const response = await request(frontendPort, '/api/servers?scope=all');
		assert.equal(observedUrl, '/servers?scope=all');
		assert.equal(response.statusCode, 200);
		assert.equal(response.headers['x-backend'], 'gpu-monitor');
		assert.equal(response.body, '{"ok":true}');
	} finally {
		await close(frontend);
		await close(backend);
	}
});

test('non-proxy requests are delegated to the adapter-node handler', async () => {
	const createMonitoringServer = await loadRuntimeServer();
	const backend = http.createServer((_req, res) => {
		res.writeHead(500);
		res.end('backend should not receive this request');
	});
	const backendPort = await listen(backend);
	const frontend = createMonitoringServer({
		backendTarget: `http://127.0.0.1:${backendPort}`,
		handler: (req, res) => {
			res.writeHead(200, { 'content-type': 'text/plain' });
			res.end(`adapter:${req.url}`);
		}
	});
	const frontendPort = await listen(frontend);

	try {
		const response = await request(frontendPort, '/dashboard');
		assert.equal(response.statusCode, 200);
		assert.equal(response.body, 'adapter:/dashboard');
	} finally {
		await close(frontend);
		await close(backend);
	}
});

test('a synchronous adapter handler failure returns 500 without terminating the server', async () => {
	const createMonitoringServer = await loadRuntimeServer();
	const backend = http.createServer((_req, res) => {
		res.writeHead(500);
		res.end();
	});
	const backendPort = await listen(backend);
	const frontend = createMonitoringServer({
		backendTarget: `http://127.0.0.1:${backendPort}`,
		handler: () => {
			throw new Error('adapter failed synchronously');
		}
	});
	const frontendPort = await listen(frontend);

	try {
		const response = await request(frontendPort, '/dashboard');
		assert.equal(response.statusCode, 500);
		assert.equal(response.body, 'Internal Server Error');
	} finally {
		await close(frontend);
		await close(backend);
	}
});

test('an unavailable backend returns a bounded 502 response without crashing the frontend', async () => {
	const createMonitoringServer = await loadRuntimeServer();
	const unavailable = http.createServer();
	const unavailablePort = await listen(unavailable);
	await close(unavailable);
	const frontend = createMonitoringServer({
		backendTarget: `http://127.0.0.1:${unavailablePort}`,
		handler: (_req, res) => {
			res.writeHead(200);
			res.end('adapter');
		}
	});
	const frontendPort = await listen(frontend);

	try {
		const response = await request(frontendPort, '/api/servers');
		assert.equal(response.statusCode, 502);
		assert.match(response.body, /Bad Gateway/);
	} finally {
		await close(frontend);
	}
});

test('WebSocket upgrades preserve /ws paths and tunnel the upgraded connection', async () => {
	const createMonitoringServer = await loadRuntimeServer();
	let observedUrl = null;
	let observedProxyConnection = null;
	const backend = http.createServer();
	backend.on('upgrade', (req, socket) => {
		observedUrl = req.url;
		observedProxyConnection = req.headers['proxy-connection'];
		const accept = createHash('sha1')
			.update(`${req.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
			.digest('base64');
		socket.write(
			[
				'HTTP/1.1 101 Switching Protocols',
				'Connection: Upgrade',
				'Upgrade: websocket',
				`Sec-WebSocket-Accept: ${accept}`,
				'X-Backend-Upgrade: yes',
				'',
				''
			].join('\r\n')
		);
		socket.end('backend-ready');
	});
	const backendPort = await listen(backend);
	const frontend = createMonitoringServer({
		backendTarget: `http://127.0.0.1:${backendPort}`,
		handler: (_req, res) => {
			res.writeHead(404);
			res.end();
		}
	});
	const frontendPort = await listen(frontend);

	try {
		const response = await new Promise((resolve, reject) => {
			const socket = net.connect(frontendPort, '127.0.0.1');
			let received = '';
			const timeout = setTimeout(() => {
				socket.destroy();
				reject(new Error('timed out waiting for proxied WebSocket upgrade'));
			}, 2000);
			socket.on('connect', () => {
				socket.write(
					[
						'GET /ws/metrics?stream=all HTTP/1.1',
						`Host: 127.0.0.1:${frontendPort}`,
						'Connection: Upgrade',
						'Upgrade: websocket',
						'Proxy-Connection: keep-alive',
						'Sec-WebSocket-Version: 13',
						'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',
						'',
						''
					].join('\r\n')
				);
			});
			socket.on('data', (chunk) => {
				received += chunk.toString('latin1');
				if (received.includes('backend-ready')) {
					clearTimeout(timeout);
					socket.destroy();
					resolve(received);
				}
			});
			socket.on('error', (error) => {
				clearTimeout(timeout);
				reject(error);
			});
		});
		assert.equal(observedUrl, '/ws/metrics?stream=all');
		assert.equal(observedProxyConnection, undefined);
		assert.match(response, /^HTTP\/1\.1 101 Switching Protocols/m);
		assert.match(response, /X-Backend-Upgrade: yes/i);
		assert.match(response, /backend-ready/);
	} finally {
		await close(frontend);
		await close(backend);
	}
});

test('a rejected WebSocket upgrade is reframed as a complete connection-close response', async () => {
	const createMonitoringServer = await loadRuntimeServer();
	const backend = http.createServer();
	backend.on('upgrade', (_req, socket) => {
		socket.end(
			[
				'HTTP/1.1 403 Forbidden',
				'Content-Type: text/plain',
				'Transfer-Encoding: chunked',
				'Connection: close',
				'',
				'6',
				'denied',
				'0',
				'',
				''
			].join('\r\n')
		);
	});
	const backendPort = await listen(backend);
	const frontend = createMonitoringServer({
		backendTarget: `http://127.0.0.1:${backendPort}`,
		handler: (_req, res) => res.end()
	});
	const frontendPort = await listen(frontend);

	try {
		const response = await new Promise((resolve, reject) => {
			const socket = net.connect(frontendPort, '127.0.0.1');
			let received = '';
			const timeout = setTimeout(() => {
				socket.destroy();
				reject(new Error('rejected WebSocket response did not close'));
			}, 2000);
			socket.on('connect', () => {
				socket.write(
					[
						'GET /ws/metrics HTTP/1.1',
						`Host: 127.0.0.1:${frontendPort}`,
						'Connection: Upgrade',
						'Upgrade: websocket',
						'Sec-WebSocket-Version: 13',
						'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',
						'',
						''
					].join('\r\n')
				);
			});
			socket.on('data', (chunk) => {
				received += chunk.toString('latin1');
			});
			socket.on('close', () => {
				clearTimeout(timeout);
				resolve(received);
			});
			socket.on('error', (error) => {
				clearTimeout(timeout);
				reject(error);
			});
		});
		assert.match(response, /^HTTP\/1\.1 403 Forbidden/m);
		assert.doesNotMatch(response, /transfer-encoding:/i);
		assert.match(response, /connection: close/i);
		assert.match(response, /\r\n\r\ndenied$/);
	} finally {
		await close(frontend);
		await close(backend);
	}
});
