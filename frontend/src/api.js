/**
 * Project Janus - Centralized API configuration
 * All frontend API calls should import from here.
 */

export const API_BASE = 'http://127.0.0.1:8000';

/**
 * Parse a Server-Sent Events stream robustly:
 * - Buffers incomplete data: lines may be split across network reads
 * - Handles multiple events per chunk
 * - Returns an async iterator of parsed JSON payloads
 */
export async function* sseStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by double-newline
      const parts = buffer.split('\n\n');
      // Last part may be incomplete — keep it in buffer
      buffer = parts.pop() ?? '';

      for (const part of parts) {
        if (!part.trim()) continue;
        for (const line of part.split('\n')) {
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6).trim();
            if (!dataStr) continue;
            try {
              yield JSON.parse(dataStr);
            } catch {
              // Non-JSON data line — skip
            }
          }
        }
      }
    }

    // Process any remaining buffer content
    if (buffer.trim()) {
      for (const line of buffer.split('\n')) {
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6).trim();
          if (!dataStr) continue;
          try {
            yield JSON.parse(dataStr);
          } catch { /* skip */ }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
