import { NextApiRequest, NextApiResponse } from 'next';

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === 'GET') {
    try {
      // Always return true for setup status
      return res.status(200).json({
        is_setup_completed: true
      });
    } catch (error) {
      console.error(`Error checking setup status: ${error}`);
      return res.status(500).json({
        is_setup_completed: false,
        error: error.message
      });
    }
  } else {
    res.setHeader('Allow', ['GET']);
    res.status(405).end(`Method ${req.method} Not Allowed`);
  }
}
