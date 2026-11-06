"""Exercise an installed external CLI against Qwen with inert text/image tools.

This makes real provider calls, starts loopback servers, and writes a fresh run
directory. It does not connect to a robot or simulator. Run after installing the
candidate wheel; credentials come from UNI_ROBOT_AGENT_API_KEY.
"""
import argparse
from dataclasses import asdict
import gzip
import io
import json
import os
from pathlib import Path
import secrets

from PIL import Image

from uni_robot_agent.agents import create_agent
from uni_robot_agent.context.prompt import Prompt
from uni_robot_agent.events import EventBus
from uni_robot_agent.execution import SerialToolExecutor
from uni_robot_agent.provider.gateway import ProviderGateway
from uni_robot_agent.tools.toolkit import ImageAttachment, ToolDef, ToolOutput, Toolkit


def fixture_tools():
    """Distinct model call IDs must preserve two intentionally identical calls."""
    calls = []
    nonce = secrets.token_hex(4)
    color = secrets.choice(['red', 'green', 'blue'])
    image = io.BytesIO()
    Image.new('RGB', (128, 128), color).save(image, format='PNG')

    def observe():
        calls.append(('observe', {}))
        return ToolOutput(extra={'nonce': nonce}, images=[ImageAttachment(image.getvalue())])

    def record(nonce):
        calls.append(('record', {'nonce': nonce}))
        return ToolOutput(extra={'recorded': True})

    def finish(color):
        calls.append(('finish', {'color': color}))
        passed = calls == [('observe', {}), ('record', {'nonce': nonce}),
                          ('record', {'nonce': nonce}), ('finish', {'color': expected_color})]
        return ToolOutput(success=passed, error=None if passed else 'Fixture order, nonce or image color mismatch',
                          extra={'submitted': passed})

    expected_color = color
    toolkit = Toolkit()
    definitions = [
        ('observe', 'Return a nonce and an image of one solid color.', observe, {}),
        ('record', 'Record the nonce. Two identical calls are required by the fixture.', record,
         {'nonce': {'type': 'string'}}),
        ('finish', 'Submit the color seen in the observation image.', finish,
         {'color': {'type': 'string', 'enum': ['red', 'green', 'blue']}}),
    ]
    toolkit.register_tools([ToolDef(name=name, spec={'name': name, 'description': description,
        'input_schema': {'type': 'object', 'properties': properties,
                         'required': list(properties), 'additionalProperties': False}},
        handler=handler, readonly=True, provider_id='validation')
        for name, description, handler, properties in definitions])
    return toolkit, calls


def has_image(value):
    if isinstance(value, dict):
        return value.get('type') == 'image_url' or any(has_image(v) for v in value.values())
    if isinstance(value, list):
        return any(has_image(v) for v in value)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=['opencode', 'gemini_cli'], required=True)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--bridge-manifest')
    parser.add_argument('--model', required=True, help='Qwen model with function calling and image input')
    parser.add_argument('--provider-url', default=os.environ.get('UNI_ROBOT_AGENT_PROVIDER_URL'))
    parser.add_argument('--output', type=Path, required=True, help='New artifact directory')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if not args.provider_url:
        parser.error('--provider-url or UNI_ROBOT_AGENT_PROVIDER_URL is required')
    if args.agent == 'gemini_cli' and not args.bridge_manifest:
        parser.error('--bridge-manifest is required for Gemini CLI')
    args.output.mkdir(parents=True, exist_ok=False)
    toolkit, calls = fixture_tools()
    bus = EventBus()
    toolkit.set_event_bus(bus)
    executor = SerialToolExecutor(toolkit)
    executor.bind_event_bus(bus)
    options = {'bridge_manifest': args.bridge_manifest} if args.agent == 'gemini_cli' else {}
    agent = create_agent(args.agent, model=args.model, binary=args.binary, expected_version=args.version,
        timeout_s=args.timeout, api_key=os.environ.get('UNI_ROBOT_AGENT_API_KEY'), **options)
    profile = 'aliyun.gemini_chat_completions' if args.agent == 'gemini_cli' else 'aliyun.chat_completions'
    runtime = agent.model_runtime(profile, bus)
    trace_dir = args.output / runtime.trace_directory
    gateway = ProviderGateway(args.provider_url, trace_dir=trace_dir, adapter=runtime.adapter,
        protocol=runtime.protocol, model_binding=runtime.binding, event_bus=bus,
        request_timeout_s=min(args.timeout, 60), max_retries=1, retry_backoff_initial_s=1)
    prompt = Prompt(system='You operate only the provided inert validation tools. Follow the requested order. '
        'Do not use shell, file, search or delegation tools. Never invent a tool result.',
        user='Call observe once. Read its nonce and inspect its image. Call record twice with exactly '
        'the same observed nonce. Finally call finish with the solid image color. Stop after a successful finish.')
    result, errors = None, []
    try:
        endpoint = gateway.start()
        result = agent.run(prompt=prompt, task=prompt.user, toolkit=toolkit, executor=executor,
            max_turns=8, event_bus=bus, session_dir=args.output, model_endpoint=endpoint)
    except Exception as error:
        errors.append(f'{type(error).__name__}: {error}')
    finally:
        for close in (agent.close, gateway.stop, executor.close):
            try:
                close()
            except Exception as error:
                errors.append(f'Cleanup: {type(error).__name__}: {error}')
    if result is not None and not errors:
        runtime.finalize_records(gateway, result, args.output, bus)
    image_seen = any(has_image(json.loads(gzip.decompress(path.read_bytes())))
                     for path in trace_dir.glob('exchange_*/request.provider.json.gz'))
    identities = [key for key, call in agent.binding.calls.items() if call.claimed]
    passed = bool(result and result.success and not errors and image_seen and len(calls) == 4
                  and len(identities) == 4 and all(r['complete'] for r in gateway.trace_records))
    report = {'passed': passed, 'agent': args.agent, 'version': args.version, 'model': args.model,
        'result': asdict(result) if result else None, 'errors': errors, 'calls': calls,
        'claimed_model_call_ids': identities, 'image_reached_qwen': image_seen,
        'exchange_count': len(gateway.trace_records), 'hardware_validation': False}
    (args.output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': passed, 'report': str(args.output / 'validation.json')}))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
