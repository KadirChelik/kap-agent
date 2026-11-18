import type { ChatDoneMeta, CitedDisclosure, ResultArtifact, TraceStep } from '../types';

export interface SseHandlers {
  onTraceStep: (step: TraceStep) => void;
  onArtifact: (artifact: ResultArtifact) => void;
  onDelta: (chunk: string) => void;
  onCitations: (citations: CitedDisclosure[]) => void;
  onDone: (meta: ChatDoneMeta) => void;
}

function dispatchEventBlock(block: string, handlers: SseHandlers): void {
  const lines = block.split(/\r?\n/);
  let eventName = 'message';
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) return;
  const payload = JSON.parse(dataLines.join('\n')) as Record<string, unknown>;

  if (eventName === 'trace_step') {
    handlers.onTraceStep(payload as unknown as TraceStep);
  } else if (eventName === 'artifact') {
    handlers.onArtifact(payload as unknown as ResultArtifact);
  } else if (eventName === 'delta') {
    handlers.onDelta(String(payload.text ?? ''));
  } else if (eventName === 'citations') {
    handlers.onCitations((payload.citations as CitedDisclosure[]) ?? []);
  } else if (eventName === 'done') {
    handlers.onDone(payload as unknown as ChatDoneMeta);
  }
}

export async function consumeChatSse(response: Response, handlers: SseHandlers): Promise<void> {
  if (!response.body) {
    const raw = await response.text();
    for (const block of raw.split(/\r?\n\r?\n/)) {
      if (block.trim()) dispatchEventBlock(block, handlers);
    }
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop() ?? '';
    for (const part of parts) {
      if (part.trim()) dispatchEventBlock(part, handlers);
    }
  }

  if (buffer.trim()) {
    dispatchEventBlock(buffer, handlers);
  }
}
