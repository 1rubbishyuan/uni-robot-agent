"""Offline configuration for the pinned Antigravity runtime."""

SDK_VERSION = '0.1.23'


class AntigravityConfig:
    model_protocol = 'gemini'

    @classmethod
    def add_cli_arguments(cls, parser):
        pass

    @classmethod
    def resolve_defaults(cls, args):
        if args.reasoning_effort is None:
            args.reasoning_effort = 'none'
        if args.provider_profile is None:
            args.provider_profile = 'aliyun.gemini_chat_completions'

    @classmethod
    def validate_config(cls, args, explicit):
        if args.interaction_mode != 'tool_calling':
            raise ValueError('Antigravity currently supports tool_calling interaction only')
        if not args.provider_url or args.provider_profile != 'aliyun.gemini_chat_completions':
            raise ValueError('Antigravity requires --provider-url and aliyun.gemini_chat_completions')
        if args.context_policy != 'identity':
            raise ValueError('Antigravity requires identity harness context')
        if args.reasoning_effort != 'none':
            raise ValueError('Antigravity Qwen validation requires reasoning-effort none')

    @classmethod
    def config_options(cls, args):
        return {'api_key': args._api_key}
