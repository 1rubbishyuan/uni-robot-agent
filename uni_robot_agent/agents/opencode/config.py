"""OpenCode options, validated without importing or starting the CLI."""


class OpenCodeConfig:
    model_protocol = 'responses'

    @classmethod
    def add_cli_arguments(cls, parser):
        group = parser.add_argument_group('OpenCode runtime')
        group.add_argument('--opencode-binary', default='opencode')
        group.add_argument('--opencode-version', help='Exact version reported by the reviewed OpenCode binary')

    @classmethod
    def resolve_defaults(cls, args):
        if args.reasoning_effort is None:
            args.reasoning_effort = 'none'
        if args.provider_profile is None:
            args.provider_profile = 'aliyun.chat_completions'

    @classmethod
    def validate_config(cls, args, explicit):
        if getattr(args, "interaction_mode", "tool_calling") != "tool_calling":
            raise ValueError("OpenCode currently supports tool_calling interaction only")
        if not args.opencode_version:
            raise ValueError('--opencode-version is required to pin the external runtime')
        if not args.provider_url:
            raise ValueError('OpenCode requires --provider-url')
        if args.provider_profile != 'aliyun.chat_completions':
            raise ValueError('OpenCode currently supports aliyun.chat_completions only')
        if args.context_policy != 'identity':
            raise ValueError('OpenCode currently supports identity context only')
        if args.reasoning_effort != 'none':
            raise ValueError('OpenCode Qwen validation currently requires --reasoning-effort none')

    @classmethod
    def config_options(cls, args):
        return {'binary': args.opencode_binary, 'expected_version': args.opencode_version,
                'api_key': args._api_key}
