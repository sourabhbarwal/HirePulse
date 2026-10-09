import express from 'express';
import Job from '../models/Job.js';
import { pushProgress } from '../services/websocket.js';

const router = express.Router();

router.post('/jobs/:jobId/progress', async (req, res) => {
  try {
    const expectedToken = process.env.INTERNAL_WORKER_TOKEN;
    const providedToken = req.get('authorization')?.replace(/^Bearer\s+/i, '');

    if (!expectedToken) {
      return res.status(503).json({ error: 'Worker progress callback is not configured' });
    }

    if (providedToken !== expectedToken) {
      return res.status(401).json({ error: 'Unauthorized' });
    }

    const progress = Array.isArray(req.body?.progress)
      ? req.body.progress.filter((step) => typeof step === 'string' && step.trim()).slice(-100)
      : [];
    const status = ['queued', 'processing', 'done', 'failed'].includes(req.body?.status)
      ? req.body.status
      : undefined;

    if (progress.length === 0 && !status) {
      return res.status(400).json({ error: 'Progress or status is required' });
    }

    const update = {};
    if (progress.length > 0) update.progress = progress;
    if (status) update.status = status;

    const job = await Job.findByIdAndUpdate(
      req.params.jobId,
      { $set: update },
      { new: true },
    ).select('_id status progress').lean();

    if (!job) {
      return res.status(404).json({ error: 'Job not found' });
    }

    pushProgress(job._id, { jobId: job._id, status: job.status, progress: job.progress || [] });
    return res.json({ ok: true });
  } catch (error) {
    console.error('Worker progress callback failed:', error);
    return res.status(500).json({ error: 'Failed to publish progress' });
  }
});

export default router;
