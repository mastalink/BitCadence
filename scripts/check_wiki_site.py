"""Read-only browser smoke for a deployed wiki; no gateway or account access."""
import argparse
import json
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    args = parser.parse_args()
    target = urlsplit(args.url)
    if target.scheme not in {'http', 'https'} or target.username or target.password or target.query:
        parser.error('Use the public wiki URL without credentials or query parameters.')
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.locator('#search-open').click()
        page.locator('#search').fill('Administration')
        page.locator('#search-results a').first.wait_for()
        count = page.locator('#search-results a').count()
        page.keyboard.press('Escape')
        page.locator('#search-dialog').wait_for(state='hidden')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('#menu').click()
        page.wait_for_function("document.getElementById('sidebar').classList.contains('open')")
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors, errors
        print(json.dumps({'search_results': count, 'escape_closes_search': True,
                          'mobile_menu': True, 'page_errors': errors}))
        browser.close()


if __name__ == '__main__':
    main()
