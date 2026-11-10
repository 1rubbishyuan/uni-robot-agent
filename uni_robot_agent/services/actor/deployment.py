"""Dispatch actor preparation through the same backend selection as serving."""
from .config import load_recipe, recipe_backend


def recipe(args):
    if not args.profile:
        raise ValueError("Actor deployment requires an explicit --profile recipe")
    selected = load_recipe(args.profile)
    recipe_backend(selected).deployment()
    return selected


def validate(args):
    recipe_backend(recipe(args)).deployment().validate(args)


def external_data(args):
    return recipe_backend(recipe(args)).deployment().external_data(args)


def prepare(context):
    recipe_backend(context.recipe).deployment().prepare(context)


def _runtime_recipe(runtime):
    return load_recipe(runtime.data.get("profile", "robodojo_pi05"))


def verify(runtime):
    return recipe_backend(_runtime_recipe(runtime)).deployment().verify(runtime)


def plan(args, runtime, output, run_id):
    selected = runtime.data.get("profile", "robodojo_pi05")
    if selected != args.profile:
        raise ValueError("Actor runtime profile differs from the requested recipe")
    definition = recipe_backend(recipe(args))
    if definition.name != recipe_backend(_runtime_recipe(runtime)).name:
        raise ValueError("Actor runtime backend differs from the requested recipe")
    return definition.deployment().plan(args, runtime, output, run_id)
