"""CLI/Python parity and backend option ownership remain offline."""
import unittest

from uni_robot_agent import AgentConfig, SessionConfig
from uni_robot_agent.cli import build_parser


class BackendConfigTest(unittest.TestCase):
    def session(self, agent):
        return SessionConfig(integration='robouse', env_url='http://127.0.0.1:18760',
                             no_sam3=True, agent=agent).resolve()

    def test_python_defaults_match_cli(self):
        parser = build_parser()
        for backend, settings in [('opencode', {'opencode_version': 'candidate'}),
                                  ('gemini_cli', {'gemini_version': 'candidate',
                                                  'gemini_bridge_manifest': 'manifest.json'})]:
            with self.subTest(backend=backend):
                resolved = self.session(AgentConfig(agent_backend=backend, model='qwen',
                    provider_url='https://provider.invalid/v1', **settings))
                self.assertEqual(resolved.reasoning_effort, 'none')
                args = parser.parse_args(['run', '--integration', 'robouse', '--agent', backend, '--model', 'qwen',
                    *[part for key, value in settings.items() for part in ('--' + key.replace('_', '-'), value)]])
                self.assertEqual(args.agent_backend, backend)
                for key in settings:
                    self.assertEqual(getattr(args, key), getattr(resolved, key))

    def test_unsupported_capabilities_are_rejected(self):
        for change in [{'reasoning_effort': 'high'}, {'opencode_version': None},
                       {'context_policy': 'sliding_window'}, {'gemini_version': 'wrong-owner'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.session(AgentConfig(**{'agent_backend': 'opencode', 'model': 'qwen',
                    'provider_url': 'https://provider.invalid/v1', 'opencode_version': 'candidate', **change}))

    def test_external_backends_reject_code_policy_before_start(self):
        for backend, settings in [('opencode', {'opencode_version': 'candidate'}),
                                  ('gemini_cli', {'gemini_version': 'candidate',
                                                  'gemini_bridge_manifest': 'manifest.json'})]:
            with self.subTest(backend=backend), self.assertRaisesRegex(ValueError, 'tool_calling'):
                SessionConfig(integration='robouse', env_url='http://127.0.0.1:18760',
                    no_sam3=True, interaction_mode='code_as_policy', agent=AgentConfig(
                        agent_backend=backend, model='qwen', provider_url='https://provider.invalid/v1',
                        **settings)).resolve()
