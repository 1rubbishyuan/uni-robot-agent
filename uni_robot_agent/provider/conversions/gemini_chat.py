"""The GenerateContent subset needed for text, image and function-tool loops."""
from __future__ import annotations

import copy
import json
from uuid import uuid4

from uni_robot_agent.provider.contracts import EncodedRequest, DecodedResponse
from uni_robot_agent.provider.protocols.chat_completions import parse_response


def _media(part):
    if 'text' in part:
        return {'type': 'text', 'text': part['text']}
    data = part['inlineData']
    return {'type': 'image_url', 'image_url': {
        'url': f"data:{data['mimeType']};base64,{data['data']}"}}


def _validate_media(part):
    if 'text' in part:
        if not isinstance(part['text'], str):
            raise ValueError('Gemini text part must contain a string')
    elif 'inlineData' in part:
        data = part['inlineData']
        if (not isinstance(data, dict) or not isinstance(data.get('mimeType'), str)
                or not data['mimeType'].startswith('image/') or not isinstance(data.get('data'), str)):
            raise ValueError('Qwen bridge supports image inlineData with string data only')
    else:
        raise ValueError('Unsupported Gemini content part: ' + ', '.join(part))


def _validated_contents(request):
    """Validate native history without constructing media URLs or Chat copies."""
    pending, seen = {}, set()
    for content in request.get('contents', []):
        if not isinstance(content, dict) or not isinstance(content.get('parts', []), list):
            raise ValueError('Gemini content requires a parts list')
        role = content.get('role', 'user')
        if role not in {'user', 'model'}:
            raise ValueError('Unsupported Gemini content role')
        if role == 'model' and pending:
            raise ValueError('Model content precedes outstanding tool results')
        has_response = has_media = False
        for part in content.get('parts', []):
            if not isinstance(part, dict):
                raise ValueError('Gemini content part must be an object')
            # The CLI's public sentinel carries no Google reasoning state.
            if 'thoughtSignature' in part and (role != 'model' or
                    part['thoughtSignature'] != 'skip_thought_signature_validator'):
                raise ValueError('Google thought signatures are not supported by the Qwen bridge')
            if part.get('thought'):
                if role != 'model' or not isinstance(part.get('text'), str):
                    raise ValueError('Thought part requires model text')
            elif 'functionCall' in part:
                call = part['functionCall']
                if not isinstance(call, dict):
                    raise ValueError('Function call must be an object')
                key, name = call.get('id'), call.get('name')
                if (role != 'model' or not isinstance(key, str) or not key or key in seen
                        or not isinstance(name, str) or not name
                        or not isinstance(call.get('args', {}), dict)):
                    raise ValueError('Function call requires unique ID, name and object arguments')
                seen.add(key)
                pending[key] = name
            elif 'functionResponse' in part:
                result = part['functionResponse']
                if not isinstance(result, dict):
                    raise ValueError('Function response must be an object')
                key = result.get('id')
                if (role != 'user' or not isinstance(key, str) or key not in pending
                        or pending[key] != result.get('name')):
                    raise ValueError('Function response does not match an outstanding call ID')
                if not isinstance(result.get('response'), dict) or not isinstance(result.get('parts', []), list):
                    raise ValueError('Function response requires an object payload and attachment list')
                del pending[key]
                has_response = True
                for attachment in result.get('parts', []):
                    if not isinstance(attachment, dict):
                        raise ValueError('Function response attachment must be an object')
                    _validate_media(attachment)
            else:
                _validate_media(part)
                has_media = True
        if role == 'user' and has_media and pending and not has_response:
            raise ValueError('All tool results must precede subsequent image or user content')
        yield content
    if pending:
        raise ValueError('History contains tool calls without matching results')


def validate_request(request):
    """Reject unsupported original content before a context policy can omit it."""
    for _ in _validated_contents(request):
        pass


def _schema(value):
    """Translate Gemini enum casing without dropping schema constraints."""
    if isinstance(value, list):
        return [_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _schema(item) for key, item in value.items() if key != 'nullable'}
    if isinstance(result.get('type'), str):
        result['type'] = result['type'].lower()
        if value.get('nullable'):
            result['type'] = [result['type'], 'null']
    return result


def encode_request(request):
    unsupported = set(request) - {
        'model', 'contents', 'systemInstruction', 'tools', 'toolConfig',
        'generationConfig', 'safetySettings', '_ura_stream', '_ura_parallel',
    }
    if unsupported:
        raise ValueError('Unsupported Gemini request fields: ' + ', '.join(sorted(unsupported)))
    if request.get('safetySettings'):
        raise ValueError('Gemini safetySettings cannot be mapped to Qwen')
    messages, pending = [], {}
    deferred_media = []
    system = request.get('systemInstruction', {}).get('parts', [])
    if system:
        if any(set(p) != {'text'} for p in system):
            raise ValueError('System instruction must contain text only')
        messages.append({'role': 'system', 'content': '\n'.join(p['text'] for p in system)})
    for content in _validated_contents(request):
        role = content.get('role', 'user')
        text_parts, calls, results, reasoning = [], [], [], []
        for part in content.get('parts', []):
            if part.get('thought'):
                reasoning.append(part['text'])
            elif 'functionCall' in part:
                call = part['functionCall']
                key = call.get('id')
                pending[key] = call['name']
                calls.append({'id': key, 'type': 'function', 'function': {
                    'name': call['name'], 'arguments': json.dumps(call.get('args', {}))}})
            elif 'functionResponse' in part:
                result = part['functionResponse']
                key = result.get('id')
                del pending[key]
                results.append({'role': 'tool', 'tool_call_id': key,
                    'content': json.dumps(result['response'], ensure_ascii=False)})
                for attachment in result.get('parts', []):
                    text_parts.append(_media(attachment))
            else:
                text_parts.append(_media(part))
        if role == 'model':
            message = {'role': 'assistant', 'content': text_parts or None}
            if calls:
                message['tool_calls'] = calls
            if reasoning:
                message['reasoning_content'] = ''.join(reasoning)
            messages.append(message)
        else:
            messages.extend(results)
            if text_parts and pending:
                deferred_media.extend(text_parts)
            elif not pending and (text_parts or deferred_media):
                messages.append({'role': 'user', 'content': deferred_media + text_parts})
                deferred_media = []

    tools = []
    for group in request.get('tools', []):
        if set(group) != {'functionDeclarations'}:
            raise ValueError('Only function declarations are supported by the Qwen bridge')
        for tool in group['functionDeclarations']:
            tools.append({'type': 'function', 'function': {
                'name': tool['name'], 'description': tool.get('description', ''),
                'parameters': _schema(tool.get('parametersJsonSchema', tool.get('parameters', {'type': 'object'})))}})
    names = [t['function']['name'] for t in tools]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate Gemini tool declarations')
    body = {'model': request['model'], 'messages': messages, 'stream': True,
            'parallel_tool_calls': request.get('_ura_parallel', True)}
    if tools:
        body['tools'] = tools
    generation = request.get('generationConfig', {})
    fields = {'temperature': 'temperature', 'topP': 'top_p',
              'topK': 'top_k', 'maxOutputTokens': 'max_tokens', 'stopSequences': 'stop'}
    thinking = generation.get('thinkingConfig')
    if thinking is not None and thinking != {'thinkingBudget': 0, 'includeThoughts': False}:
        raise ValueError('Qwen bridge only supports explicitly disabled Gemini thinking')
    if set(generation) - set(fields) - {'thinkingConfig'}:
        raise ValueError('Unsupported Gemini generationConfig: ' + ', '.join(sorted(set(generation) - set(fields))))
    body.update({target: generation[key] for key, target in fields.items() if key in generation})
    choice = request.get('toolConfig', {}).get('functionCallingConfig', {})
    mode = choice.get('mode', 'AUTO')
    if mode not in {'AUTO', 'NONE', 'ANY'}:
        raise ValueError('Unsupported function calling mode')
    allowed = choice.get('allowedFunctionNames')
    if allowed:
        if mode != 'ANY' or len(allowed) != 1 or allowed[0] not in names:
            raise ValueError('Only one declared forced function can be mapped to Qwen')
        body['tool_choice'] = {'type': 'function', 'function': {'name': allowed[0]}}
    elif tools:
        body['tool_choice'] = {'AUTO': 'auto', 'NONE': 'none', 'ANY': 'required'}[mode]
    return EncodedRequest(body, {'stream': request.get('_ura_stream', False)}, copy.deepcopy(body))


def decode_response(raw, state):
    chat = parse_response(raw)
    choice = chat['choices'][0]
    if choice.get('finish_reason') not in {'stop', 'tool_calls', 'length'}:
        raise ValueError('Qwen response has no supported terminal reason')
    message, parts = choice['message'], []
    if message.get('reasoning_content'):
        parts.append({'thought': True, 'text': message['reasoning_content']})
    if message.get('content'):
        parts.append({'text': message['content']})
    ids = set()
    for call in message.get('tool_calls') or []:
        key = call.get('id')
        args = json.loads(call['function']['arguments'])
        if not key or key in ids or not isinstance(args, dict) or choice['finish_reason'] == 'length':
            raise ValueError('Invalid, duplicate or truncated Qwen tool call')
        ids.add(key)
        # The CLI prefixes IDs itself. Supply that canonical form so history,
        # ACP notifications and MCP metadata all refer to the same identity.
        name = call['function']['name']
        identity = key if key.startswith(name + '__') else name + '__' + key
        parts.append({'functionCall': {'id': identity, 'name': name, 'args': args}})
    usage = chat.get('usage') or {}
    response = {'responseId': chat.get('id') or uuid4().hex, 'candidates': [{
        'index': 0, 'content': {'role': 'model', 'parts': parts},
        'finishReason': 'MAX_TOKENS' if choice['finish_reason'] == 'length' else 'STOP',
    }], 'usageMetadata': {'promptTokenCount': usage.get('prompt_tokens', 0),
        'candidatesTokenCount': usage.get('completion_tokens', 0),
        'totalTokenCount': usage.get('total_tokens', 0)}}
    encoded = json.dumps(response, ensure_ascii=False).encode()
    if state['stream']:
        encoded = b'data: ' + encoded + b'\n\n'
    return DecodedResponse(encoded, raw)
