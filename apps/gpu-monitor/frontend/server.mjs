import http from 'node:http';
import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const HOP_BY_HOP_HEADERS = new Set([
	'connection',
	'keep-alive',
	'proxy-authenticate',
	'proxy-authorization',
	'proxy-connection',
	'te',
	'trailer',
	'transfer-encoding',
	'upgrade'
]);
const PROXY_TIMEOUT_MS = 15_000;

function parseBackendTarget(value) {
	const target = new URL(value);
	const hostname = target.hostname === '[::1]' ? '::1' : target.hostname;
	if (target.protocol !== 'http:') {
		throw new Error('MONITORING_API_TARGET must use http on a loopback address');
	}
	if (!['127.0.0.1', '::1', 'localhost'].includes(hostname)) {
		throw new Error('MONITORING_API_TARGET must use a loopback address');
	}
	if (target.pathname !== '/' || target.search || target.hash || target.username || target.password) {
		throw new Error('MONITORING_API_TARGET must contain only scheme, loopback host, and port');
	}
	return {
		protocol: target.protocol,
		hostname,
		port: target.port,
		host: target.host
	};
}

function matchesPrefix(rawUrl, prefix) {
	try {
		const { pathname } = new URL(rawUrl ?? '/', 'http://gpu-monitor.invalid');
		return pathname === prefix || pathname.startsWith(`${prefix}/`);
	} catch {
		return false;
	}
}

function rewriteApiPath(rawUrl) {
	const value = rawUrl ?? '/';
	const queryIndex = value.indexOf('?');
	const pathname = queryIndex === -1 ? value : value.slice(0, queryIndex);
	const query = queryIndex === -1 ? '' : value.slice(queryIndex);
	const rewritten = pathname.slice('/api'.length);
	return `${rewritten || '/'}${query}`;
}

function sanitizedHeaders(headers, { preserveUpgrade = false } = {}) {
	const result = {};
	for (const [name, value] of Object.entries(headers)) {
		if (value === undefined) continue;
		const lowerName = name.toLowerCase();
		const requiredForUpgrade =
			preserveUpgrade && (lowerName === 'connection' || lowerName === 'upgrade');
		if (HOP_BY_HOP_HEADERS.has(lowerName) && !requiredForUpgrade) continue;
		result[name] = value;
	}
	return result;
}

function proxyHttp(req, res, target) {
	const headers = sanitizedHeaders(req.headers);
	headers.host = target.host;
	const upstream = http.request(
		{
			protocol: target.protocol,
			hostname: target.hostname,
			port: target.port,
			method: req.method,
			path: rewriteApiPath(req.url),
			headers
		},
		(upstreamResponse) => {
			res.writeHead(
				upstreamResponse.statusCode ?? 502,
				sanitizedHeaders(upstreamResponse.headers)
			);
			upstreamResponse.pipe(res);
		}
	);

	const fail = () => {
		if (res.headersSent) {
			res.destroy();
			return;
		}
		res.writeHead(502, {
			'cache-control': 'no-store',
			'content-type': 'text/plain; charset=utf-8'
		});
		res.end('Bad Gateway');
	};

	upstream.setTimeout(PROXY_TIMEOUT_MS, () => upstream.destroy(new Error('backend timeout')));
	upstream.on('error', fail);
	req.on('aborted', () => upstream.destroy());
	req.pipe(upstream);
}

function writeRawResponseHead(socket, response) {
	const statusCode = response.statusCode ?? 502;
	const statusMessage = response.statusMessage ?? 'Bad Gateway';
	let head = `HTTP/${response.httpVersion} ${statusCode} ${statusMessage}\r\n`;
	for (let index = 0; index < response.rawHeaders.length; index += 2) {
		head += `${response.rawHeaders[index]}: ${response.rawHeaders[index + 1]}\r\n`;
	}
	socket.write(`${head}\r\n`);
}

function writeConnectionCloseResponseHead(socket, response) {
	const statusCode = response.statusCode ?? 502;
	const statusMessage = response.statusMessage ?? 'Bad Gateway';
	const headers = sanitizedHeaders(response.headers);
	headers.connection = 'close';
	let head = `HTTP/${response.httpVersion} ${statusCode} ${statusMessage}\r\n`;
	for (const [name, value] of Object.entries(headers)) {
		for (const item of Array.isArray(value) ? value : [value]) {
			head += `${name}: ${item}\r\n`;
		}
	}
	socket.write(`${head}\r\n`);
}

function proxyWebSocket(req, clientSocket, clientHead, target) {
	const headers = sanitizedHeaders(req.headers, { preserveUpgrade: true });
	headers.host = target.host;
	const upstreamRequest = http.request({
		protocol: target.protocol,
		hostname: target.hostname,
		port: target.port,
		method: req.method,
		path: req.url,
		headers
	});

	const closeBoth = () => {
		upstreamRequest.destroy();
		clientSocket.destroy();
	};

	upstreamRequest.setTimeout(PROXY_TIMEOUT_MS, closeBoth);
	upstreamRequest.on('upgrade', (upstreamResponse, upstreamSocket, upstreamHead) => {
		upstreamRequest.setTimeout(0);
		writeRawResponseHead(clientSocket, upstreamResponse);
		if (upstreamHead.length) clientSocket.write(upstreamHead);
		if (clientHead.length) upstreamSocket.write(clientHead);
		upstreamSocket.on('error', () => clientSocket.destroy());
		clientSocket.on('error', () => upstreamSocket.destroy());
		upstreamSocket.on('close', () => clientSocket.destroy());
		clientSocket.on('close', () => upstreamSocket.destroy());
		upstreamSocket.pipe(clientSocket).pipe(upstreamSocket);
	});
	upstreamRequest.on('response', (upstreamResponse) => {
		upstreamRequest.setTimeout(0);
		writeConnectionCloseResponseHead(clientSocket, upstreamResponse);
		upstreamResponse.on('error', () => clientSocket.destroy());
		upstreamResponse.on('end', () => clientSocket.end());
		upstreamResponse.pipe(clientSocket, { end: false });
	});
	upstreamRequest.on('error', () => clientSocket.destroy());
	upstreamRequest.end();
}

function sendHandlerFailure(res) {
	if (res.headersSent) {
		res.destroy();
		return;
	}
	res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' });
	res.end('Internal Server Error');
}

function sendNotFound(res) {
	res.writeHead(404, {
		'cache-control': 'no-store',
		'content-type': 'text/plain; charset=utf-8'
	});
	res.end('Not found');
}

export function createMonitoringServer({ handler, backendTarget }) {
	if (typeof handler !== 'function') {
		throw new TypeError('handler must be a function');
	}
	const target = parseBackendTarget(backendTarget);
	const server = http.createServer((req, res) => {
		if (matchesPrefix(req.url, '/debug')) {
			sendNotFound(res);
			return;
		}
		if (matchesPrefix(req.url, '/api')) {
			proxyHttp(req, res, target);
			return;
		}
		try {
			Promise.resolve(handler(req, res)).catch(() => sendHandlerFailure(res));
		} catch {
			sendHandlerFailure(res);
		}
	});
	server.on('upgrade', (req, socket, head) => {
		if (!matchesPrefix(req.url, '/ws')) {
			socket.destroy();
			return;
		}
		proxyWebSocket(req, socket, head, target);
	});
	return server;
}

function parsePort(value) {
	const port = Number.parseInt(value, 10);
	if (!Number.isInteger(port) || port < 1 || port > 65_535) {
		throw new Error(`invalid frontend port: ${value}`);
	}
	return port;
}

async function start() {
	const { handler } = await import('./build/handler.js');
	const backendPort = process.env.GPU_MONITOR_BACKEND_PORT || '8001';
	const backendTarget =
		process.env.MONITORING_API_TARGET || `http://127.0.0.1:${backendPort}`;
	const host = process.env.HOST || '127.0.0.1';
	const port = parsePort(process.env.PORT || '3000');
	const server = createMonitoringServer({ handler, backendTarget });
	server.listen(port, host, () => {
		console.log(`GPU Monitor frontend listening on http://${host}:${port}`);
	});
}

let isEntryPoint = false;
if (typeof process.argv[1] === 'string') {
	try {
		isEntryPoint =
			realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
	} catch {
		isEntryPoint = false;
	}
}

if (isEntryPoint) {
	start().catch((error) => {
		console.error(error);
		process.exitCode = 1;
	});
}
