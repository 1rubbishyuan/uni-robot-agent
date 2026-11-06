// Compatibility hook for the Gemini ACP loop; never infer identity from arguments.
import type { DiscoveredMCPTool, ToolResult } from '@google/gemini-cli-core';
import type { Part } from '@google/genai';

export async function callRobotTool(
  tool: DiscoveredMCPTool,
  args: Record<string, unknown>,
  callId: string,
  signal: AbortSignal,
): Promise<ToolResult> {
  const url = process.env.UNI_ROBOT_AGENT_MCP_URL;
  const authorization = process.env.UNI_ROBOT_AGENT_MCP_AUTH;
  if (!url || !authorization || !callId) {
    throw new Error('Robot bridge requires its endpoint, authorization and explicit call ID');
  }
  const parsed = new URL(url);
  if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1') {
    throw new Error('Robot bridge endpoint must be an owned loopback server');
  }
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json', Accept: 'application/json', Authorization: authorization,
      'X-Uni-Robot-Agent-Bridge': 'uni-robot-agent-gemini-acp-call-id-v1',
    },
    body: JSON.stringify({
      jsonrpc: '2.0', id: callId, method: 'tools/call',
      params: { name: tool.serverToolName, arguments: args, _meta: { 'uni_robot_agent/call_id': callId } },
    }),
    signal,
  });
  if (!response.ok) throw new Error(`Robot MCP HTTP ${response.status}`);
  const envelope = await response.json();
  if (envelope.jsonrpc !== '2.0' || envelope.id !== callId || envelope.error) {
    throw new Error('Robot MCP returned an invalid or failed response');
  }
  const result = envelope.result;
  if (!result || !Array.isArray(result.content)) throw new Error('Robot MCP omitted content');
  const parts: Part[] = [];
  const display: string[] = [];
  for (const block of result.content) {
    if (block.type === 'text' && typeof block.text === 'string') {
      parts.push({ text: block.text });
      display.push(block.text);
    } else if (block.type === 'image' && typeof block.data === 'string'
        && typeof block.mimeType === 'string' && block.mimeType.startsWith('image/')) {
      parts.push({ inlineData: { mimeType: block.mimeType, data: block.data } });
    } else {
      throw new Error('Unsupported robot MCP result block');
    }
  }
  // Throwing feeds Gemini's native failed-call history and ACP error notification.
  // It must never cause this bridge to retry an uncertain physical invocation.
  if (result.isError) throw new Error(display.join('\n') || 'Robot tool failed');
  return { llmContent: parts, returnDisplay: display.join('\n') };
}
