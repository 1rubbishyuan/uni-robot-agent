"""Offline configuration for the explicitly patched Gemini CLI runtime."""


class GeminiConfig:
    model_protocol = 'gemini'

    @classmethod
    def add_cli_arguments(cls, parser):
        group = parser.add_argument_group('Gemini CLI runtime')
        group.add_argument('--gemini-binary', default='gemini')
        group.add_argument('--gemini-version', help='Exact version of the reviewed Gemini build')
        group.add_argument('--gemini-bridge-manifest', help='Manifest sealing the patched executable bundle')

    @classmethod
    def resolve_defaults(cls, args):
        if args.reasoning_effort is None:
            args.reasoning_effort = 'none'
        if args.provider_profile is None:
            args.provider_profile = 'aliyun.gemini_chat_completions'

    @classmethod
    def validate_config(cls, args, explicit):
        if not args.gemini_version or not args.gemini_bridge_manifest:
            raise ValueError('Gemini CLI requires --gemini-version and --gemini-bridge-manifest')
        if not args.provider_url:
            raise ValueError('Gemini CLI requires --provider-url')
        if args.provider_profile != 'aliyun.gemini_chat_completions':
            raise ValueError('Gemini CLI currently supports aliyun.gemini_chat_completions only')
        if args.context_policy != 'identity' or args.reasoning_effort != 'none':
            raise ValueError('Gemini CLI Qwen bridge requires identity context and reasoning-effort none')

    @classmethod
    def config_options(cls, args):
        return {'binary': args.gemini_binary, 'expected_version': args.gemini_version,
                'bridge_manifest': args.gemini_bridge_manifest, 'api_key': args._api_key}
