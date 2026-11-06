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
