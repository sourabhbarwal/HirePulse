import jwt from 'jsonwebtoken';
import { WebSocketServer } from 'ws';
import Job from '../models/Job.js';

const clients = new Map();

export const setupProgressWebSocket = (httpServer) => {
  const wss = new WebSocketServer({ server: httpServer });

  wss.on('connection', async (ws, req) => {
    const url = new URL(req.url, 'http://x');
    const jobId = url.searchParams.get('jobId');
    const token = url.searchParams.get('token');
    let decoded;

    try {
      decoded = jwt.verify(token, process.env.JWT_SECRET);
    } catch {
      ws.close(4001, 'Unauthorized');
      return;
    }

    if (!jobId) {
      ws.close(4000, 'Missing jobId');
      return;
    }

    try {
      const job = await Job.findOne({ _id: jobId, userId: decoded.id }).select('_id').lean();
      if (!job) {
        ws.close(4003, 'Forbidden');
        return;
      }
    } catch (error) {
      console.error('WS job ownership check failed:', error);
      ws.close(4003, 'Forbidden');
      return;
    }

    if (!clients.has(jobId)) clients.set(jobId, new Set());
    clients.get(jobId).add(ws);
    console.log(`WS connected: ${jobId}`);

    ws.on('close', () => {
      const sockets = clients.get(jobId);
      if (sockets) {
        sockets.delete(ws);
        if (sockets.size === 0) clients.delete(jobId);
      }
    });
    ws.on('error', (error) => console.error('WS error', error));
  });

  return wss;
};

export const pushProgress = (jobId, payload) => {
  const sockets = clients.get(jobId);
  if (!sockets) return;

  const message = JSON.stringify(payload);
  for (const ws of sockets) {
    if (ws.readyState === 1) ws.send(message);
  }
};
