// Run with Node after transpiling robotBridge.ts into the supplied module path.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const source = await readFile(new URL('../uni_robot_agent/agents/opencode/bridge.js', import.meta.url), 'utf8');
const { RobotBridge } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const hooks = await RobotBridge();
const input = { tool: 'ura_move', callID: 'model-call' };
const args = { direction: 'left' };
await hooks['tool.execute.before'](input, { args });
assert.equal(args._uni_robot_agent_call, 'model-call');
await hooks['tool.execute.after']({ ...input, args });
assert.deepEqual(args, { direction: 'left' });
await assert.rejects(() => hooks['tool.execute.before']({ tool: 'ura_move' }, { args: {} }));
process.env.UNI_ROBOT_AGENT_SYSTEM_PROMPT = 'robot system';
const system = { system: ['ambient system'] };
await hooks['experimental.chat.system.transform']({}, system);
assert.deepEqual(system.system, ['robot system']);

// The CLI may persist a snapshot while the private MCP identity is attached.
// Cleanup of the live callback object cannot remove it from that history copy.
const history = { messages: [{ info: { role: 'assistant' }, parts: [] }] };
for (const status of ['pending', 'running', 'completed', 'error']) {
  const callback = { direction: 'left' };
  const callID = `call-${status}`;
  await hooks['tool.execute.before']({ tool: 'ura_move', callID }, { args: callback });
  history.messages[0].parts.push({ type: 'tool', tool: 'ura_move', callID,
    state: { status, input: structuredClone(callback) } });
  if (status === 'completed') {
    await hooks['tool.execute.after']({ tool: 'ura_move', callID, args: callback });
    assert.deepEqual(callback, { direction: 'left' });
  }
}
const nativePart = { type: 'tool', tool: 'bash', callID: 'native-call',
  state: { input: { _uni_robot_agent_call: 'ordinary-native-argument' } } };
history.messages[0].parts.push(nativePart, { type: 'text', text: 'Keep user content.' });
const persistedParts = history.messages[0].parts;
await hooks['experimental.chat.messages.transform']({}, history);
for (const part of history.messages[0].parts.slice(0, 4)) {
  assert.deepEqual(part.state.input, { direction: 'left' });
  assert.equal(part.callID, `call-${part.state.status}`);
}
for (const part of persistedParts.slice(0, 4)) {
  assert.equal(part.state.input._uni_robot_agent_call, part.callID);
}
assert.equal(nativePart.state.input._uni_robot_agent_call, 'ordinary-native-argument');
assert.equal(history.messages[0].parts.at(-1).text, 'Keep user content.');

const { callRobotTool } = await import(pathToFileURL(process.argv[2]));
process.env.UNI_ROBOT_AGENT_MCP_URL = 'http://127.0.0.1:1234/mcp/';
process.env.UNI_ROBOT_AGENT_MCP_AUTH = 'Bearer fixture-token';
let invocations = 0;
let isError = false;
globalThis.fetch = async (url, options) => {
  invocations++;
  const body = JSON.parse(options.body);
  assert.equal(body.id, 'model-call');
  assert.equal(body.params._meta['uni_robot_agent/call_id'], 'model-call');
  assert.deepEqual(body.params.arguments, { direction: 'left' });
  assert.equal(options.headers.Authorization, 'Bearer fixture-token');
  return { ok: true, json: async () => ({ jsonrpc: '2.0', id: body.id, result: {
    isError, content: [{ type: 'image', mimeType: 'image/png', data: 'fixture' },
                      { type: 'text', text: 'receipt' }],
  } }) };
};
const tool = { serverToolName: 'move' };
const signal = new AbortController().signal;
const result = await callRobotTool(tool, args, 'model-call', signal);
assert.equal(result.llmContent[0].inlineData.data, 'fixture');
assert.equal(result.returnDisplay, 'receipt');
isError = true;
await assert.rejects(() => callRobotTool(tool, args, 'model-call', signal), /receipt/);
assert.equal(invocations, 2); // One HTTP attempt per callback, including the failure.
console.log('OpenCode hook identity and Gemini MCP text/image/error bridge: passed');
