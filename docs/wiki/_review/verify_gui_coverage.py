"""Verify mapped documentation anchors and source locations; does not run the app.

From the repository root:
  python docs/wiki/_review/verify_gui_coverage.py
For a staging directory pass --inventory, --docs-root and --repo explicitly.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def heading_ids(text):
    counts = {}
    found = set()
    for line in text.splitlines():
        match = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
        if not match:
            continue
        label = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', match[1])
        slug = re.sub(r'[^\w\- ]', '', label.lower()).replace(' ', '-')
        number = counts.get(slug, 0)
        counts[slug] = number + 1
        found.add(slug if not number else f'{slug}-{number}')
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--docs-root', type=Path)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--strict-hashes', action='store_true', help='Fail if sources differ from recorded baseline')
    args = parser.parse_args()
    repo = args.repo.resolve()
    docs = (args.docs_root or repo/'docs/wiki').resolve()
    inventory = args.inventory or docs/'_review/gui-feature-coverage.json'
    data = json.loads(inventory.read_text(encoding='utf-8'))
    failures = []
    warnings = []
    for entry in data['entries']:
        page, _, anchor = entry['documentation'].partition('#')
        target = (docs/page).resolve()
        if not target.is_relative_to(docs) or not target.is_file():
            failures.append(f'{entry["id"]}: missing documentation {page}')
        elif anchor and anchor not in heading_ids(target.read_text(encoding='utf-8')):
            failures.append(f'{entry["id"]}: missing anchor {page}#{anchor}')
        source = (repo/entry['source']).resolve()
        if not source.is_relative_to(repo) or not source.is_file():
            failures.append(f'{entry["id"]}: missing source {entry["source"]}')
        elif not 1 <= entry['line'] <= len(source.read_text(encoding='utf-8').splitlines()):
            failures.append(f'{entry["id"]}: source line out of bounds')
    for item in data['sources']:
        source = repo/item['path']
        if source.is_file() and hashlib.sha256(source.read_text(encoding='utf-8').replace('\r\n','\n').encode('utf-8')).hexdigest() != item['sha256']:
            (failures if args.strict_hashes else warnings).append('Source changed: '+item['path'])
    ids = [entry['id'] for entry in data['entries']]
    if len(ids) != len(set(ids)):
        failures.append('Duplicate feature identifiers')
    for issue in failures:
        print('FAIL '+issue)
    for issue in warnings:
        print('WARN '+issue)
    print(json.dumps({'entries_checked':len(ids),'failures':len(failures),'warnings':len(warnings),'claim':'Static coverage links and source bounds only; no runtime behavior checked.'}))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
