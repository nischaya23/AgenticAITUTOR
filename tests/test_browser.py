"""Optional real-browser test; owns its own temporary demo server and database."""
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
import urllib.request
import pytest


@pytest.mark.browser
@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Opt-in browser check')
def test_all_interventions_materials_and_mobile(tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    root=Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    env={**os.environ,'DEMO_MODE':'true','DATABASE_URL':f'sqlite:///{tmp_path / "ui.db"}',
         'DATA_DIR':str(tmp_path/'materials')}
    server=subprocess.Popen([sys.executable,'-m','uvicorn','backend.main:app','--host','127.0.0.1',
                             '--port',str(port)],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base+'/api/health',timeout=.2).close();break
            except OSError:
                if server.poll() is not None:
                    raise AssertionError('Demo server stopped unexpectedly.')
                time.sleep(.1)
        else:
            raise AssertionError('Demo server did not start.')
        with playwright.sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'],
                executable_path=os.getenv('BROWSER_EXECUTABLE') or shutil.which('chromium'))
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base)
            page.get_by_text('Connected',exact=True).wait_for()
            page.locator('#workspace').fill('Keep the first match inside [left, right).')
            page.locator('[data-tool=hint]').click()
            page.locator('.message .label',has_text='HINT').wait_for()
            for kind in ('explanation','highlight','visualization','guiding_question'):
                page.locator(f'[data-tool={kind}]').click()
                page.locator('.message .label',has_text=kind.replace('_',' ').upper()).wait_for()
            page.locator('.diagram').wait_for()
            page.get_by_text('Show highlighted passage',exact=True).click()
            page.locator('#highlight-preview mark').wait_for()
            assert page.locator('#highlight-preview mark').inner_text()=='Keep the first match inside [left, right).'
            page.locator('#answer').fill('1');page.locator('#attempt-form button').click()
            page.get_by_text('✓ Correct.',exact=False).wait_for()
            page.locator('#upload').set_input_files({'name':'queues.md','mimeType':'text/markdown','buffer':b'# Queues\nA queue uses FIFO.'})
            page.locator('#material-list button',has_text='queues.md').wait_for()
            page.reload();page.get_by_text('Connected',exact=True).wait_for()
            assert page.locator('#workspace').input_value()=='Keep the first match inside [left, right).'
            assert page.locator('.message .label').count()>=5
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.locator('#end-session').click()
            page.locator('.content',has_text='Practiced Binary Search').wait_for()
            assert not errors,errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
