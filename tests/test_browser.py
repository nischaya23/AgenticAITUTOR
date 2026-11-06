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
         'DATA_DIR':str(tmp_path/'materials'),'FAILURE_THRESHOLD':'1','STALL_SECONDS':'0',
         'AGGREGATION_SECONDS':'0','TYPING_GRACE_SECONDS':'0'}
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
            assert page.locator('button').count()==7
            page.locator('#upload').set_input_files({'name':'course.md','mimeType':'text/markdown',
                'buffer':b'# Binary Search\nBinary search narrows a sorted interval.'})
            playwright.expect(page.locator('#workspace')).to_be_enabled()
            playwright.expect(page.locator('#material-name')).to_have_text('course.md')
            playwright.expect(page.locator('#graph svg')).to_be_visible()
            playwright.expect(page.locator('#mastery')).to_contain_text('Not assessed yet')
            page.locator('#workspace').fill('Keep the first match in the interval.')
            page.wait_for_timeout(1200)  # Allow the documented one-second edit debounce.
            page.reload()
            playwright.expect(page.locator('#workspace')).to_have_value('Keep the first match in the interval.')
            page.locator('#answer').fill('wrong');page.locator('#submit').click()
            playwright.expect(page.locator('.intervention strong')).to_contain_text('guiding question')
            playwright.expect(page.locator('#mastery')).to_contain_text('40%')
            old=page.locator('#problem').inner_text()
            page.locator('#answer').fill('1');page.locator('#submit').click()
            playwright.expect(page.locator('#problem')).not_to_have_text(old)
            playwright.expect(page.locator('#mastery')).to_contain_text('52%')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors,errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
