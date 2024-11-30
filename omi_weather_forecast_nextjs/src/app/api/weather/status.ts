import { NextApiRequest, NextApiResponse } from 'next';
import { messageBuffer } from './utils'; // Assume messageBuffer is implemented in utils

const startTime = Date.now();

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === 'GET') {
    return res.status(200).json({
      active_sessions: Object.keys(messageBuffer.buffers).length,
      uptime: Date.now() - startTime
    });
  } else {
    res.setHeader('Allow', ['GET']);
    res.status(405).end(`Method ${req.method} Not Allowed`);
  }
}
