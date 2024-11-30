import { NextApiRequest, NextApiResponse } from 'next';
import { MessageBuffer } from './utils'; // Assume MessageBuffer is implemented in utils
import { getOpenAIResponse } from './openai'; // Assume getOpenAIResponse is implemented in openai

const messageBuffer = new MessageBuffer();
const notificationCooldowns: Record<string, number> = {};
const NOTIFICATION_COOLDOWN = 10; // 10 seconds cooldown

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === 'POST') {
    console.info('Received webhook POST request');
    const data = req.body;
    console.info(`Received data: ${JSON.stringify(data)}`);

    const sessionId = data.session_id;
    const uid = req.query.uid as string;
    console.info(`Processing request for session_id: ${sessionId}, uid: ${uid}`);

    if (!sessionId) {
      console.error('No session_id provided in request');
      return res.status(400).json({ status: 'error', message: 'No session_id provided' });
    }

    const currentTime = Date.now();
    const bufferData = messageBuffer.getBuffer(sessionId);
    const segments = data.segments || [];
    let hasProcessed = false;

    console.debug(`Current buffer state for session ${sessionId}: ${JSON.stringify(bufferData)}`);

    if (bufferData.triggerDetected && !bufferData.responseSent) {
      const timeSinceLastNotification = currentTime - (notificationCooldowns[sessionId] || 0);
      if (timeSinceLastNotification < NOTIFICATION_COOLDOWN * 1000) {
        console.info(`Cooldown active. ${NOTIFICATION_COOLDOWN - timeSinceLastNotification / 1000}s remaining`);
        return res.status(200).json({ status: 'success' });
      }
    }

    for (const segment of segments) {
      if (!segment.text || hasProcessed) continue;

      const text = segment.text.toLowerCase().trim();
      console.info(`Processing text segment: '${text}'`);

      if (TRIGGER_PHRASES.some(trigger => text.includes(trigger.toLowerCase())) && !bufferData.triggerDetected) {
        console.info(`Complete trigger phrase detected in session ${sessionId}`);
        bufferData.triggerDetected = true;
        bufferData.triggerTime = currentTime;
        bufferData.collectedQuestion = [];
        bufferData.responseSent = false;
        bufferData.partialTrigger = false;
        notificationCooldowns[sessionId] = currentTime;

        const questionPart = text.split('omi,').pop()?.trim() || '';
        if (questionPart) {
          bufferData.collectedQuestion.push(questionPart);
          console.info(`Collected question part from trigger: ${questionPart}`);
        }
        continue;
      }

      if (!bufferData.triggerDetected) {
        if (PARTIAL_FIRST.some(part => text.endsWith(part.toLowerCase()))) {
          console.info(`First part of trigger detected in session ${sessionId}`);
          bufferData.partialTrigger = true;
          bufferData.partialTriggerTime = currentTime;
          continue;
        }

        if (bufferData.partialTrigger) {
          const timeSincePartial = currentTime - bufferData.partialTriggerTime;
          if (timeSincePartial <= 2000) {
            if (PARTIAL_SECOND.some(part => text.includes(part.toLowerCase()))) {
              console.info(`Complete trigger detected across segments in session ${sessionId}`);
              bufferData.triggerDetected = true;
              bufferData.triggerTime = currentTime;
              bufferData.collectedQuestion = [];
              bufferData.responseSent = false;
              bufferData.partialTrigger = false;

              const questionPart = text.split('omi,').pop()?.trim() || '';
              if (questionPart) {
                bufferData.collectedQuestion.push(questionPart);
                console.info(`Collected question part from second trigger part: ${questionPart}`);
              }
              continue;
            }
          } else {
            bufferData.partialTrigger = false;
          }
        }
      }

      bufferData.collectedQuestion.push(text);
      console.info(`Collecting question part: ${text}`);
      console.info(`Current collected question: ${bufferData.collectedQuestion.join(' ')}`);

      const timeSinceTrigger = currentTime - bufferData.triggerTime;
      const shouldProcess =
        (timeSinceTrigger > QUESTION_AGGREGATION_TIME * 1000 && bufferData.collectedQuestion.length > 0) ||
        (bufferData.collectedQuestion.length > 0 && text.includes('?')) ||
        (timeSinceTrigger > QUESTION_AGGREGATION_TIME * 1.5 * 1000);

      if (shouldProcess && bufferData.collectedQuestion.length > 0) {
        let fullQuestion = bufferData.collectedQuestion.join(' ').trim();
        if (!fullQuestion.endsWith('?')) fullQuestion += '?';

        console.info(`Processing complete question: ${fullQuestion}`);
        const response = await getOpenAIResponse(fullQuestion);
        console.info(`Got response from OpenAI: ${response}`);

        bufferData.triggerDetected = false;
        bufferData.triggerTime = 0;
        bufferData.collectedQuestion = [];
        bufferData.responseSent = true;
        bufferData.partialTrigger = false;
        hasProcessed = true;

        return res.status(200).json({ message: response });
      }
    }

    return res.status(200).json({ status: 'success' });
  } else {
    res.setHeader('Allow', ['POST']);
    res.status(405).end(`Method ${req.method} Not Allowed`);
  }
}
