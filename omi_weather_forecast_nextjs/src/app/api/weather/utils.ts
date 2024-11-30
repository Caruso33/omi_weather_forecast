import { Mutex } from "async-mutex"

interface BufferData {
  messages: string[]
  triggerDetected: boolean
  triggerTime: number
  collectedQuestion: string[]
  responseSent: boolean
  partialTrigger: boolean
  partialTriggerTime: number
  lastActivity: number
}

export class MessageBuffer {
  private buffers: Record<string, BufferData> = {}
  private lock = new Mutex()
  private cleanupInterval = 300000 // 5 minutes in milliseconds
  private lastCleanup = Date.now()

  public async getBuffer(sessionId: string): Promise<BufferData> {
    const currentTime = Date.now()

    // Cleanup old sessions periodically
    if (currentTime - this.lastCleanup > this.cleanupInterval) {
      await this.cleanupOldSessions()
    }

    return this.lock.runExclusive(() => {
      if (!this.buffers[sessionId]) {
        this.buffers[sessionId] = {
          messages: [],
          triggerDetected: false,
          triggerTime: 0,
          collectedQuestion: [],
          responseSent: false,
          partialTrigger: false,
          partialTriggerTime: 0,
          lastActivity: currentTime,
        }
      } else {
        this.buffers[sessionId].lastActivity = currentTime
      }
      return this.buffers[sessionId]
    })
  }

  private async cleanupOldSessions(): Promise<void> {
    const currentTime = Date.now()
    await this.lock.runExclusive(() => {
      const expiredSessions = Object.keys(this.buffers).filter(
        (sessionId) =>
          currentTime - this.buffers[sessionId].lastActivity > 3600000 // 1 hour in milliseconds
      )
      for (const sessionId of expiredSessions) {
        delete this.buffers[sessionId]
      }
      this.lastCleanup = currentTime
    })
  }
}
