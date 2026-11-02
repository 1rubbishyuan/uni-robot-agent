"""Project recorded facts into UI state without guessing call or frame identity."""
from __future__ import annotations


def compact(value, limit=16000):
    import json
    text = json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + '\n… output truncated in monitor; full output remains in trajectory.jsonl'


def project(events, snapshots, records, name):
    meta, result = records.get('monitor', {}), records.get('result', {})
    native = records.get('native_outcome', {})
    calls, batches, models, frames, messages = {}, {}, {}, [], []
    queue = {}
    started = meta.get('started_at')
    ended = meta.get('finished_at')
    task, integration, model = meta.get('task') or name, meta.get('integration', ''), meta.get('model', '')
    instruction = meta.get('instruction', '')
    measured_steps = None
    canonical = None

    def batch(key, timestamp):
        return batches.setdefault(key, dict(id=key, timestamp=timestamp, call_ids=[]))

    def call(key, label='', timestamp=None):
        value = calls.setdefault(key, dict(id=key, name=label, status='submitted', timestamp=timestamp,
                                          batch_id='', source='agent'))
        if label:
            value['name'] = label
        return value

    for i, e in enumerate(events):
        kind, t = e.get('type'), e.get('timestamp', e.get('timestamp_unix_s'))
        if t is not None:
            started = min(started, t) if started is not None else t
        if kind in ('SessionStart', 'AgentRunStarted', 'SessionOpened'):
            instruction = e.get('task', e.get('instruction', instruction))
            integration, model = e.get('integration', integration), e.get('model', model)
            canonical = e.get('tool_event_source', canonical)
        if kind == 'ToolCallBatch' or (kind == 'AgentActivity' and e.get('kind') == 'tool_batch'):
            bid = e.get('batch_id') or e.get('exchange_id') or f'batch_{i}'
            b = batch(bid, t)
            specs = e.get('tool_calls') or []
            for spec in specs:
                cid = spec.get('call_id')
                if not cid:
                    continue
                c = call(cid, spec.get('name', ''), t)
                c.update(batch_id=bid, arguments=compact(spec.get('arguments', {})))
                if cid not in b['call_ids']:
                    b['call_ids'].append(cid)
            b['names'] = e.get('tool_names', b.get('names', []))
        elif kind == 'AgentActivity' and e.get('kind') in ('tool_started', 'tool_completed', 'tool_failed'):
            cid = e.get('item_id')
            if cid:
                c = call(cid, (e.get('tool_names') or ['tool'])[0], t)
                if e.get('transport_call_id'):
                    c['transport_call_id'] = e['transport_call_id']
                c['sdk_status'] = e['kind'].removeprefix('tool_')
                # SDK completion can merely acknowledge a queued background action.
                c['status'] = {'tool_started': 'dispatching', 'tool_completed': 'acknowledged', 'tool_failed': 'failed'}[e['kind']]
                if e.get('exchange_id'):
                    c['batch_id'] = e['exchange_id']
                    b = batch(e['exchange_id'], t)
                    if cid not in b['call_ids']:
                        b['call_ids'].append(cid)
        elif kind == 'ToolExecution':
            cid = e.get('call_id') or e['execution_id']
            c = call(cid, e.get('tool_name', ''), t)
            c.update(source='execution', execution_id=e['execution_id'], resource=e.get('resource', ''),
                     job_id=e.get('job_id', ''), arguments=compact(e.get('arguments', {})))
            if e.get('batch_id'):
                c['batch_id'] = e['batch_id']
                b = batch(c['batch_id'], t)
                if cid not in b['call_ids']:
                    b['call_ids'].append(cid)
            phase = e['phase']
            c['execution_status'] = {'queued': 'queued', 'started': 'running', 'finished': 'completed' if e.get('success') else 'failed', 'cancelled': 'cancelled'}[phase]
            c['status'] = c['execution_status']
            c[{'queued': 'queued_at', 'started': 'started_at', 'finished': 'finished_at', 'cancelled': 'finished_at'}[phase]] = t
            if e.get('error'):
                c['error'] = e['error']
        elif kind == 'ToolResult':
            cid = e.get('call_id') or e.get('execution_id')
            if cid:
                c = call(cid, e.get('tool_name', ''), t)
            else:
                # Legacy records cannot establish identity across concurrent invocations.
                c = call(f'result_{i}', e.get('tool_name', ''), t)
                c['source'] = 'legacy'
            output = e.get('output', {})
            c.update(output=compact(output), success=e.get('success'))
            if isinstance(output, dict):
                native_steps = output.get('state', {}).get('native_steps')
                if native_steps is not None:
                    measured_steps = native_steps

            if not c.get('execution_status'):
                c['status'] = 'completed' if e.get('success', True) else 'failed'
            if e.get('batch_id'):
                c['batch_id'] = e['batch_id']
            for image in e.get('images', []):
                if image.get('path'):
                    frames.append(dict(id=f'event_{i}_{len(frames)}', timestamp=output.get('captured_at', t) if isinstance(output, dict) else t, camera=image.get('name', 'observation'),
                        snapshot_id=output.get('snapshot_id') if isinstance(output, dict) else None,
                        state_version=output.get('image_state_version') if isinstance(output, dict) else None,
                        path=image['path'], batch_id=c.get('batch_id', ''), call_id=cid or '', association='tool output'))
        elif kind == 'ToolQueueSnapshot':
            queue = e.get('resources', {})
        elif kind == 'ModelRequest':
            m = models.setdefault(e['exchange_id'], dict(id=e['exchange_id'], agent_id=e.get('agent_id', '')))
            m['started_at' if e['phase'] == 'started' else 'finished_at'] = t
            if e['phase'] == 'finished':
                m['success'] = e.get('success')
        elif kind in ('AssistantText', 'ReasoningSummary', 'ErrorEvent'):
            messages.append(dict(type=kind, timestamp=t, text=compact(e.get('text', e.get('message', '')), 6000)))
        elif kind == 'SessionClosed':
            ended = t

    # A transport receipt can arrive after the background job has already completed.
    # Merge by the exact returned identity, never by names, arguments or timestamps.
    aliases = {}
    for cid, c in list(calls.items()):
        transport = c.get('transport_call_id')
        if transport and transport != cid and transport in calls:
            runtime = calls.pop(transport)
            c.update({k: v for k, v in runtime.items() if k not in {'id', 'batch_id', 'name'}})
            aliases[transport] = cid
    for frame in frames:
        cid = aliases.get(frame.get('call_id'), frame.get('call_id'))
        if cid in calls:
            frame['call_id'] = cid
            frame['batch_id'] = calls[cid].get('batch_id', '')
    for c in calls.values():
        if c.get('execution_status'):
            c['status'] = c['execution_status']
    for snapshot in snapshots:
        for camera, image in snapshot.get('cameras', {}).items():
            if image.get('path'):
                frames.append(dict(id=snapshot['snapshot_id'] + ':' + camera, timestamp=snapshot.get('captured_at'),
                    camera=camera, path=image['path'], snapshot_id=snapshot['snapshot_id'],
                    state_version=snapshot.get('state_version'), association='published observation', batch_id=''))
    frames.sort(key=lambda f: f.get('timestamp') or 0)
    latest = snapshots[-1] if snapshots else {}
    steps = latest.get('state', {}).get('native_steps', measured_steps)
    identity = result.get('episode_identity') or {}
    budget = meta.get('max_episode_steps', identity.get('environment', {}).get('max_episode_steps', native.get('step_limit')))
    task = result.get('task') or task
    integration, model = result.get('integration', integration), result.get('model', model)
    instruction = result.get('instruction') or instruction
    outcome = result.get('task_success')
    status = meta.get('state', 'unknown')
    if result:
        status = 'success' if outcome is True else 'failed' if outcome is False else 'finished'
        if result.get('accepted') is False or (outcome is None and result.get('error')):
            status = 'error'
        if result.get('steps', native.get('steps')) is not None:
            steps = result.get('steps', native.get('steps'))
        if ended is None and started is not None and result.get('elapsed_s') is not None:
            ended = started + result['elapsed_s']
    elif records.get('supervisor', {}).get('state') in ('deployment_failed', 'environment_failed', 'episode_timeout', 'cancelled'):
        status = records['supervisor']['state']
    elif meta.get('state') in ('starting', 'deploying', 'running', 'finishing'):
        status = meta['state']
    elif not meta and events:
        status = 'unknown'
    # An archive with unfinished work must not animate indefinitely as a live run.
    if status not in ('starting', 'deploying', 'running', 'finishing'):
        queue = {}
    return dict(name=name, task=task, instruction=instruction, integration=integration, model=model,
        status=status, started_at=started, finished_at=ended, task_success=outcome,
        score=result.get('score', native.get('native_score')), steps=steps, max_steps=budget,
        episode_timeout_s=meta.get('episode_timeout_s', result.get('episode_timeout_s')), elapsed_s=result.get('elapsed_s'),
        failure_category=result.get('failure_category'), error=result.get('error') or meta.get('error') or records.get('supervisor', {}).get('error'),
        canonical_tool_source=canonical, calls=list(calls.values())[-1000:], batches=list(batches.values())[-500:],
        models=list(models.values())[-1000:], frames=frames if result else frames[-2000:], queue=queue, messages=messages[-100:],
        total_calls=len(calls), total_frames=len(frames), total_batches=len(batches),
        history_limited=len(calls)>1000 or (not result and len(frames)>2000) or len(batches)>500)
