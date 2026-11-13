"""The public help site must stay navigable, searchable and safe to build."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('wiki_site', ROOT / 'scripts/build_wiki.py')
wiki = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wiki)

def test_links_images_and_heading_anchors_are_rewritten():
    content = wiki.render_markdown('# My guide\n\n## Next step\n\n[Next](next.md#thing)\n\n![View](img/demo.png)\n')
    assert 'href="next.html#thing"' in content
    assert 'id="next-step"' in content
    assert 'loading="lazy"' in content

def test_script_in_markdown_is_not_executable():
    assert '<script>alert(' not in wiki.render_markdown('<script>alert(1)</script>')

def test_safe_reference_anchors_survive_rendering():
    rendered=wiki.render_markdown('<a id="cli-mco-score"></a>\n\n## Score\n')
    assert '<a id="cli-mco-score"></a>' in rendered

def test_build_produces_searchable_site_with_no_broken_local_links(tmp_path):
    source = tmp_path/'docs';source.mkdir()
    (source/'Home.md').write_text('# Welcome\n\n[Start](start.md)\n')
    (source/'start.md').write_text('# Start here\n\n## Connect\n\nUse the Connect button.\n')
    output=tmp_path/'site'
    result=wiki.build(source,output)
    assert result['pages']==2 and not result['broken_links']
    assert (output/'index.html').exists()
    records=json.loads((output/'search-index.json').read_text())
    assert any(r.get('page')=='Start here' and r['title']=='Connect' and 'Connect button' in r['text'] for r in records)

def test_missing_document_link_fails_the_build(tmp_path):
    source=tmp_path/'docs';source.mkdir()
    (source/'Home.md').write_text('# Home\n\n[Missing](nope.md)\n')
    assert wiki.build(source,tmp_path/'out')['broken_links']
