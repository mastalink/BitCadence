"""Verify the wiki's command/parameter coverage against the live Typer tree.

No command callbacks, parameter callbacks, shell completion, or gateway requests
are executed. Run with the repository's Python environment:
  python docs/wiki/_review/verify_cli_coverage.py --repo .
"""
from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
from pathlib import Path


def portable(value):
    if isinstance(value, (list, tuple)):
        return [portable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): portable(item) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value).replace(str(Path.home()), "~")
    return text.replace("\\", "/") if isinstance(value, Path) else text


def anchor(path):
    return "cli-" + "-".join(path)


def live_inventory(repo):
    sys.path.insert(0, str(repo / "src"))
    import typer.main
    from mco.cli import app

    root = typer.main.get_command(app)
    entries = []

    def visit(command, path, parents):
        callback = inspect.unwrap(command.callback) if command.callback else None
        source_file = inspect.getsourcefile(callback) if callback else None
        try:
            source = str(Path(source_file).resolve().relative_to(repo.resolve())) if source_file else None
        except ValueError:
            source = source_file
        params = list(command.params)
        # --help is synthesized by Click, so it isn't in command.params.
        # Asking for its Option object does not execute its eager callback.
        help_context = command.context_class(
            command, help_option_names=command.context_settings.get("help_option_names")
        )
        help_param = command.get_help_option(help_context)
        if help_param is not None:
            params.append(help_param)
        values = []
        for param in params:
            is_option = "Option" in type(param).__name__
            tokens = list(getattr(param, "opts", [])) + list(getattr(param, "secondary_opts", []))
            values.append({
                "name": param.name,
                "kind": "option" if is_option else "argument",
                "tokens": tokens,
                "type": str(getattr(param.type, "name", type(param.type).__name__)).upper(),
                "required": bool(param.required),
                "default": portable(param.default),
                "multiple": bool(getattr(param, "multiple", False)),
                "nargs": param.nargs,
                "is_flag": bool(getattr(param, "is_flag", False)),
                "hidden": bool(getattr(param, "hidden", False)),
                "help": inspect.cleandoc(getattr(param, "help", None) or ""),
                "prompt": portable(getattr(param, "prompt", None)),
                "envvar": portable(getattr(param, "envvar", None)),
                "choices": portable(getattr(param.type, "choices", None)),
                "min": getattr(param.type, "min", None),
                "max": getattr(param.type, "max", None),
                "path_exists": getattr(param.type, "exists", None),
                "path_readable": getattr(param.type, "readable", None),
                "path_writable": getattr(param.type, "writable", None),
                "anchor": anchor(path) + "-param-" + param.name.replace("_", "-"),
            })
        entry = {
            "path": " ".join(path), "aliases": [" ".join(["bitcadence", *path[1:]])],
            "anchor": anchor(path), "group": hasattr(command, "commands"),
            "hidden": bool(command.hidden),
            "help": inspect.cleandoc(command.help or ""),
            "source": source.replace("\\", "/") if source else None,
            "line": inspect.getsourcelines(callback)[1] if callback else None,
            "inherited_groups": parents,
            "parameters": values,
            "allow_extra_args": command.context_settings.get("allow_extra_args", False),
            "ignore_unknown_options": command.context_settings.get("ignore_unknown_options", False),
        }
        entries.append(entry)
        if hasattr(command, "commands"):
            for name, child in sorted(command.commands.items()):
                visit(child, [*path, name], [*parents, " ".join(path)])

    visit(root, ["mco"], [])
    return {"schema_version": 1, "entry_points": ["mco", "bitcadence"], "commands": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=Path, default=Path(__file__).with_name("cli-coverage.json"))
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--dump", action="store_true", help="Print live metadata; never write files.")
    args = parser.parse_args()
    live = live_inventory(args.repo.resolve())
    if args.dump:
        print(json.dumps(live, indent=2))
        return 0
    expected = json.loads(args.inventory.read_text(encoding="utf-8"))
    reference = args.reference or args.inventory.parent.parent / "mco-cli-reference.md"
    if not reference.exists():
        reference = args.inventory.with_name("mco-cli-reference.md")
    markdown = reference.read_text(encoding="utf-8")
    errors = []
    # Anchors are explicit so Markdown renderer slug rules cannot change coverage.
    anchors = re.findall(r'<a id="([^"]+)"></a>', markdown)
    if len(anchors) != len(set(anchors)):
        errors.append("Duplicate documentation anchors")
    documented = {entry["path"]: entry for entry in expected["commands"]}
    current = {entry["path"]: entry for entry in live["commands"]}
    for path in sorted(set(current) | set(documented)):
        if path not in documented:
            errors.append(f"Undocumented command: {path}")
            continue
        if path not in current:
            errors.append(f"Stale command: {path}")
            continue
        actual, saved = current[path], documented[path]
        for field in ("aliases", "group", "hidden", "help", "inherited_groups", "allow_extra_args", "ignore_unknown_options"):
            if actual[field] != saved[field]:
                errors.append(f"Changed {field}: {path}")
        actual_params = {param["name"]: param for param in actual["parameters"]}
        saved_params = {param["name"]: param for param in saved["parameters"]}
        for name in sorted(set(actual_params) | set(saved_params)):
            if actual_params.get(name) != saved_params.get(name):
                errors.append(f"Missing or changed argument/option: {path} / {name}")
        required_anchors = [saved["anchor"], *(p["anchor"] for p in saved["parameters"])]
        for item in required_anchors:
            if item not in anchors:
                errors.append(f"Missing documentation anchor: {item}")
        # The command's section must name every spelling, not only carry an ID.
        start = markdown.find(f'<a id="{saved["anchor"]}"></a>')
        following = re.search(r'\n<a id="cli-[^"]+"></a>\n## ', markdown[start + 1:])
        section = markdown[start:start + 1 + following.start()] if following else markdown[start:]
        for param in saved["parameters"]:
            if param["kind"] == "argument":
                if f"`{param['name'].upper()}`" not in section:
                    errors.append(f"Undocumented positional argument: {path} / {param['name']}")
                continue
            for token in param["tokens"]:
                if token not in section:
                    errors.append(f"Undocumented spelling: {path} / {token}")
    for error in errors:
        print("FAIL " + error)
    count = sum(len(entry["parameters"]) for entry in live["commands"])
    print(f"{len(current)} command/group paths; {count} arguments/options; {len(errors)} coverage errors.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
