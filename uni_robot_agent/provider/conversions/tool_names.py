"""Map Responses tool identities to flat names and restore declared routes."""

from __future__ import annotations

import copy
import json
from typing import Any


def qualified_tool_name(name: str, namespace: str | None = None) -> str:
    return f"{namespace}__{name}" if namespace else name


def flatten_tools(
    tools: list[Any], routes: dict[str, dict[str, Any]], namespace: str | None = None,
) -> list[Any]:
    flattened: list[Any] = []
    for tool in tools:
        if not isinstance(tool, dict):
            flattened.append(tool)
            continue
        if tool.get("type") == "namespace":
            if namespace is not None:
                raise ValueError("Nested tool namespaces are not supported by Responses routing")
            children = flatten_tools(tool.get("tools", []), routes, tool["name"])
            for child in children:
                if child not in flattened:
                    flattened.append(child)
            continue
        name = "web_search" if tool.get("type") == "web_search" else tool.get("name")
        value = copy.deepcopy(tool)
        if name:
            flat_name = qualified_tool_name(name, namespace)
            route = {"name": name, "namespace": namespace, "type": tool["type"], "tool": copy.deepcopy(tool)}
            previous = routes.get(flat_name)
            if previous is not None and previous != route:
                raise ValueError(f"Tool name collision: {flat_name!r}")
            routes[flat_name] = route
            if "name" in value:
                value["name"] = flat_name
        if value not in flattened:
            flattened.append(value)
    return flattened


def flatten_request(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    result = copy.deepcopy(request)
    routes: dict[str, dict[str, Any]] = {}
    tools = flatten_tools(result.get("tools", []), routes)
    items = [item for item in result.get("input", []) if isinstance(item, dict)]
    # Codex may declare a tool after its calls in history. Build all routes first.
    for item in items:
        if item.get("type") == "additional_tools":
            item["tools"] = flatten_tools(item.get("tools", []), routes)
            for tool in item["tools"]:
                if tool not in tools:
                    tools.append(tool)
    if tools or "tools" in result:
        result["tools"] = tools
    for item in items:
        if item.get("type") in {"function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"}:
            if item.get("name"):
                identity = (item.get("namespace"), item["name"])
                route = routes.get(qualified_tool_name(item["name"], item.get("namespace")))
                if route and (route["namespace"], route["name"]) != identity:
                    raise ValueError(f"Tool history identity collision: {identity!r}")
            _qualify_name(item)
    _flatten_choice(result.get("tool_choice"), routes)
    return result, routes


def _qualify_name(item: dict[str, Any]) -> None:
    if item.get("name"):
        item["name"] = qualified_tool_name(item["name"], item.pop("namespace", None))


def _flatten_choice(choice: Any, routes: dict[str, dict[str, Any]]) -> None:
    if not isinstance(choice, dict):
        return
    if choice.get("type") == "web_search":
        if routes.get("web_search", {}).get("type") != "web_search":
            raise ValueError("Tool choice is not declared: native web_search")
    elif choice.get("name"):
        identity = (choice.get("namespace"), choice["name"])
        route = routes.get(qualified_tool_name(choice["name"], choice.get("namespace")))
        if route is None or (route["namespace"], route["name"]) != identity or route["type"] != choice.get("type"):
            raise ValueError(f"Tool choice is not declared: {identity!r}")
        _qualify_name(choice)
    for tool in choice.get("tools", []):
        _flatten_choice(tool, routes)


def restore_response(value: Any, routes: dict[str, dict[str, Any]]) -> Any:
    """Restore only protocol fields; never interpret tool arguments or schema objects."""
    result = copy.deepcopy(value)

    def restore(item: Any) -> Any:
        if isinstance(item, list):
            return [restore(child) for child in item]
        if not isinstance(item, dict):
            return item
        kind = item.get("type")
        if kind in ("function_call_output", "custom_tool_call_output"):
            return item
        if kind in ("function_call", "custom_tool_call"):
            route = routes.get(item.get("name"))
            if route is None:
                return item
            if route["type"] == "web_search":
                arguments = item.get("arguments", "{}")
                # Responses announces a call before its argument stream is complete.
                params = {} if item.get("status") == "in_progress" else (
                    json.loads(arguments) if isinstance(arguments, str) else arguments
                )
                return {
                    "type": "web_search_call", "id": item.get("id") or item.get("call_id"),
                    "status": item.get("status", "completed"),
                    "action": {"type": "search", "query": params.get("query", "")},
                }
            item["name"] = route["name"]
            if route["namespace"] is not None:
                item["namespace"] = route["namespace"]
            if kind == "function_call" and route["type"] == "custom":
                item["type"] = "custom_tool_call"
                item["input"] = json.loads(item.pop("arguments"))["input"]
            return item
        for key in ("response", "item", "output"):
            if key in item:
                item[key] = restore(item[key])
        if isinstance(item.get("tools"), list):
            item["tools"] = _restore_tools(item["tools"], routes)
        return item

    return restore(result)


def _restore_tools(tools: list[Any], routes: dict[str, dict[str, Any]]) -> list[Any]:
    restored: list[Any] = []
    groups: dict[str, dict[str, Any]] = {}
    for tool in tools:
        route = routes.get(tool.get("name")) if isinstance(tool, dict) else None
        if route is None:
            restored.append(tool)
            continue
        declaration = copy.deepcopy(route["tool"])
        namespace = route["namespace"]
        if namespace is None:
            restored.append(declaration)
        else:
            if namespace not in groups:
                groups[namespace] = {"type": "namespace", "name": namespace, "tools": []}
                restored.append(groups[namespace])
            groups[namespace]["tools"].append(declaration)
    return restored
