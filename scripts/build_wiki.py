"""Build the static, searchable user wiki from Markdown. No live gateway access."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import unicodedata
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
GROUPS = [
    ('Start here', ['Home', 'quick-start', 'install-first-run', 'everyday-commands', 'connecting-console', 'desktop-app-and-tray']),
    ('Get work done', ['Console-home', 'Ask', 'Connect-an-AI', 'Helpers', 'job-board-and-tasks', 'projects-dashboard', 'Approvals', 'Schedules', 'Score-live']),
    ('Learn the system', ['drumline-memory', 'drumline-agent-exchange', 'scores-and-autonomy', 'jev-routing', 'workflows-and-flow-control']),
    ('Manage your setup', ['Settings', 'settings-and-configuration', 'agent-fleet-and-presence', 'workers-and-daemon-listening', 'scheduling-and-loops', 'services-and-fleet', 'sidecars-and-fleet-daemons', 'approvals-and-governance']),
    ('Reference & product review', ['mco-cli-reference', 'gui-reference', 'coverage', 'UX-REVIEW']),
]

def slug(text):
    text = unicodedata.normalize('NFKC', text).lower()
    text = re.sub(r'[^\w\s-]', '', text, flags=re.UNICODE)
    return re.sub(r'\s+', '-', text.strip())

def markdown():
    md = MarkdownIt('commonmark', {'html': True}).enable(['table', 'strikethrough'])
    old_link = md.renderer.rules.get('link_open')
    def link_open(tokens, idx, opts, env):
        token = tokens[idx]; href = token.attrGet('href') or ''
        if re.match(r'^(?:javascript|data|vbscript):', href, re.I):
            token.attrSet('href', '#')
        else:
            href = re.sub(r'(^|/)Home\.md(?=#|$)', r'\1index.md', href)
            token.attrSet('href', re.sub(r'\.md(?=#|$)', '.html', href))
            if href.startswith('https://'):
                token.attrSet('rel', 'noopener')
        return md.renderer.renderToken(tokens, idx, opts, env)
    md.renderer.rules['link_open'] = link_open
    original_image = md.renderer.rules['image']
    def image(tokens, idx, opts, env):
        tokens[idx].attrSet('loading', 'lazy'); tokens[idx].attrSet('decoding', 'async')
        return original_image(tokens, idx, opts, env)
    md.renderer.rules['image'] = image
    def safe_html(content):
        # The generated reference uses explicit anchors, including inside tables.
        return bool(re.fullmatch(r'(?:<br\s*/?>|<a id="[a-z0-9-]+">(?:</a>)?|</a>)\s*', content, re.I))
    md.renderer.rules['html_inline'] = lambda t, i, o, e: t[i].content if safe_html(t[i].content) else html.escape(t[i].content)
    md.renderer.rules['html_block'] = lambda t, i, o, e: t[i].content if safe_html(t[i].content) else '<pre>' + html.escape(t[i].content) + '</pre>'
    return md

def tokens_with_ids(text):
    md = markdown(); tokens = md.parse(text); seen = defaultdict(int)
    for i, token in enumerate(tokens):
        if token.type == 'heading_open':
            title = tokens[i+1].content
            base = slug(title); number = seen[base]; seen[base] += 1
            token.attrSet('id', base + (f'-{number}' if number else ''))
    return md, tokens

def render_markdown(text):
    md, tokens = tokens_with_ids(text)
    return md.renderer.render(tokens, md.options, {})

def file_url(rel):
    return 'index.html' if str(rel) == 'Home.md' else str(rel.with_suffix('.html')).replace('\\', '/')

def shell(title, content, nav, toc, prefix, source):
    title = html.escape(title)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · BitCadence Help</title><meta name="description" content="Step-by-step BitCadence instructions, command reference and troubleshooting.">
<link rel="stylesheet" href="{prefix}wiki.css"><script src="{prefix}wiki.js" defer></script>
<link rel="canonical" href="https://bitcadence.ai/wiki/{html.escape(source)}"></head><body>
<a class="skip" href="#content">Skip to guide</a><header><a class="brand" href="{prefix}index.html"><span class="mark" aria-hidden="true">╱</span>BitCadence <span class="help">Help</span></a><button id="menu" aria-expanded="false" aria-controls="sidebar">Guides</button><button id="search-open" class="search-open" aria-label="Search guides and commands">Search guides & commands <kbd>⌘ K</kbd></button><a class="product-link" href="https://bitcadence.ai/">Product ↗</a></header>
<div class="layout"><nav id="sidebar" aria-label="Help topics">{nav}</nav><main id="content"><div class="breadcrumb">BITCADENCE / USER GUIDE</div><article>{content}</article><footer><a href="{prefix}index.html">Help home</a><span>Instructions checked against source · November 2026</span><a href="#content">Back to top ↑</a></footer></main><aside aria-label="On this page"><b>On this page</b>{toc}</aside></div>
<dialog id="search-dialog"><div class="search-bar"><input id="search" type="search" placeholder="What would you like to do?" aria-label="Search guides and commands" autocomplete="off"><button id="search-close" aria-label="Close search">Esc</button></div><div id="search-status" role="status">Type a task, command or feature.</div><div id="search-results"></div></dialog><div id="announcement" class="sr-only" role="status"></div></body></html>'''

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids = set(); self.links = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs: self.ids.add(attrs['id'])
        if tag in ('a', 'img'):
            v = attrs.get('href' if tag == 'a' else 'src')
            if v: self.links.append(v)

def validate(output):
    pages = {}
    for p in output.rglob('*.html'):
        parser = Links(); parser.feed(p.read_text(encoding='utf-8')); pages[p.resolve()] = parser
    broken = []
    for page, parser in pages.items():
        for link in parser.links:
            uri = urlsplit(link)
            if uri.scheme or uri.netloc or link.startswith('/'):
                continue
            target = (page.parent / unquote(uri.path)).resolve() if uri.path else page
            if not target.exists():
                broken.append(f'{page.relative_to(output.resolve())}: {link}')
            elif uri.fragment and target in pages and unquote(uri.fragment) not in pages[target].ids:
                broken.append(f'{page.relative_to(output.resolve())}: missing anchor {link}')
    return broken

def build(source=ROOT/'docs/wiki', output=ROOT/'website/wiki'):
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    pages = []
    for path in sorted(source.rglob('*.md')):
        rel = path.relative_to(source); text = path.read_text(encoding='utf-8-sig')
        match = re.search(r'^#\s+(.+)', text, re.M)
        title = re.sub(r'[*`]', '', match.group(1)) if match else path.stem
        pages.append({'path': path, 'rel': rel, 'text': text, 'title': title, 'url': file_url(rel)})
    by_name = {p['path'].stem: p for p in pages}
    search = []
    for p in pages:
        depth = len(p['rel'].parts)-1; prefix = '../'*depth
        nav = []
        used = set()
        for name, keys in GROUPS:
            entries = []
            for key in keys:
                item = by_name.get(key)
                if not item: continue
                used.add(key)
                label = {'Home':'Help home','mco-cli-reference':'All CLI commands','gui-reference':'Every screen & control','coverage':'Coverage & verification','UX-REVIEW':'Usability review'}.get(key,item['title'])
                entries.append(f'<a href="{prefix}{html.escape(item["url"])}"'+(' aria-current="page"' if item==p else '')+'>'+html.escape(label)+'</a>')
            if entries:nav.append('<div class="nav-group"><h2>'+name+'</h2>'+''.join(entries)+'</div>')
        extras = [x for x in pages if x['path'].stem not in used and x['rel'].parts[0] != '_review']
        if extras:
            nav.append('<details class="nav-group"><summary>More guides</summary>'+''.join(f'<a href="{prefix}{html.escape(x["url"])}">{html.escape(x["title"])}</a>' for x in extras)+'</details>')
        md, tokens = tokens_with_ids(p['text'])
        toc = ''.join(f'<a href="#{t.attrGet("id")}">{html.escape(tokens[i+1].content)}</a>' for i,t in enumerate(tokens) if t.type=='heading_open' and t.tag=='h2')
        content = md.renderer.render(tokens, md.options, {})
        # Outside-wiki documentation keeps a working repository link instead of a broken local path.
        def outside(m):
            href = m.group(1)
            uri = urlsplit(href)
            if not uri.path.startswith('../'): return m.group(0)
            target = (p['path'].parent / unquote(uri.path)).resolve()
            try: target.relative_to(source.resolve()); return m.group(0)
            except ValueError: pass
            try:
                repo_rel = target.relative_to(ROOT.resolve())
                repo_rel = str(repo_rel).replace('\\','/').replace('.html','.md')
                return 'href="https://github.com/mastalink/BitCadence/blob/main/'+html.escape(repo_rel)+('#'+uri.fragment if uri.fragment else '')+'"'
            except ValueError:
                return m.group(0)
        content = re.sub(r'href="([^"]+)"', outside, content)
        dest = output/p['url']; dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(shell(p['title'],content,''.join(nav),toc,prefix,p['url']),encoding='utf-8')
        # Section records let searches land on the command or feature, not the top of a long page.
        current = {'title':p['title'],'url':p['url'],'text':''}
        for i,t in enumerate(tokens):
            if t.type=='heading_open' and t.tag in ('h2','h3'):
                if current['text']:search.append(current)
                current={'title':re.sub(r'[*`]', '', tokens[i+1].content),'page':p['title'],'url':p['url']+'#'+t.attrGet('id'),'text':''}
            elif t.type in ('inline','fence','code_block'):
                current['text'] += t.content+'\n'
        search.append(current)
    img = source/'img'
    if img.exists():shutil.copytree(img,output/'img',dirs_exist_ok=True)
    for name in ('cli-coverage.json','gui-feature-coverage.json'):
        record = source/'_review'/name
        if record.exists():
            (output/'_review').mkdir(exist_ok=True)
            shutil.copy2(record,output/'_review'/name)
    for name in ('wiki.css','wiki.js'):
        asset = ROOT/'scripts/wiki_assets'/name
        if asset.exists():shutil.copy2(asset,output/name)
    (output/'search-index.json').write_text(json.dumps(search,ensure_ascii=False),encoding='utf-8')
    (output/'404.html').write_text(shell('Guide not found','<h1>Let’s find the right guide.</h1><p>This page has moved or does not exist. Search for the task or <a href="index.html">start at Help home</a>.</p>','','','', '404.html'),encoding='utf-8')
    return {'pages':len(pages),'search_sections':len(search),'broken_links':validate(output)}

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=ROOT/'docs/wiki');parser.add_argument('--output',type=Path,default=ROOT/'website/wiki');args=parser.parse_args()
    result=build(args.source,args.output);print(json.dumps(result,indent=2,ensure_ascii=True));raise SystemExit(bool(result['broken_links']))
