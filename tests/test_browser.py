"""Optional real-browser test; owns its own temporary mocked-provider server and database."""
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
    env={**os.environ,'DATABASE_URL':f'sqlite:///{tmp_path / "ui.db"}',
         'PYTHONPATH':str(root/'tests')+os.pathsep+str(root),'DATA_DIR':str(tmp_path/'materials'),'LLM_PROVIDER':'openai'}
    server=subprocess.Popen([sys.executable,'-m','uvicorn','browser_server:app','--host','127.0.0.1',
                             '--port',str(port)],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base+'/api/health',timeout=.2).close();break
            except OSError:
                if server.poll() is not None:
                    raise AssertionError('Test server stopped unexpectedly.')
                time.sleep(.1)
        else:
            raise AssertionError('Test server did not start.')
        with playwright.sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'],
                executable_path=os.getenv('BROWSER_EXECUTABLE') or shutil.which('chromium'))
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base)
            page.get_by_role('button', name='Sign up', exact=True).click()
            page.get_by_label('Email', exact=True).fill('student@example.com')
            page.get_by_label('Password', exact=True).fill('test-password')
            page.get_by_role('button', name='Create account', exact=True).click()
            editor=page.get_by_role('textbox', name='Your response text')
            page.locator('#upload').set_input_files({'name':'course.md','mimeType':'text/markdown',
                'buffer':b'# Binary Search\nBinary search narrows a sorted interval.'})
            playwright.expect(editor).to_be_editable()
            playwright.expect(page.locator('button[data-tool]')).to_have_count(5)
            playwright.expect(page.locator('#material-name')).to_have_text('course.md')
            page.get_by_role('tab', name='Learning map', exact=True).click()
            playwright.expect(page.locator('#graph .react-flow__node')).to_be_visible()
            page.get_by_role('tab', name='Workspace', exact=True).click()
            playwright.expect(page.locator('#mastery')).to_contain_text('Not assessed yet')
            editor.fill('Keep the first match in the interval.')
            page.get_by_text('Optional node-and-arrow diagram', exact=True).click()
            response=page.get_by_role('region',name='Your response',exact=True)
            response.get_by_role('button',name='Add node',exact=True).click()
            response.get_by_role('button',name='Add node',exact=True).click()
            response.get_by_label('Arrow from',exact=True).select_option(label='Node 1')
            response.get_by_label('Arrow to',exact=True).select_option(label='Node 2')
            response.get_by_role('button',name='Connect nodes').click()
            response.get_by_label('Label for Node 1',exact=True).fill('Start')
            response.get_by_label('X position for Start',exact=True).fill('35')
            page.wait_for_timeout(1200)  # Allow the documented one-second edit debounce.
            page.reload()
            playwright.expect(editor).to_have_text('Keep the first match in the interval.')
            playwright.expect(response.locator('.diagram-nodes>li')).to_have_count(2)
            playwright.expect(response.get_by_label('Response diagram arrows',exact=True)).to_contain_text('Start → Node 2')
            playwright.expect(response.get_by_label('X position for Start',exact=True)).to_have_value('35')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('#submit').click()
            page.locator('[data-tool=guiding_question]').click()
            playwright.expect(page.get_by_text('guiding question', exact=True).last).to_be_visible()
            playwright.expect(page.locator('#mastery')).to_contain_text('48%')
            playwright.expect(page.get_by_role('region',name='Possible knowledge gaps')).to_contain_text('assessed submission')
            page.locator('[data-tool=highlight]').click()
            playwright.expect(page.locator('#workspace .annotation')).to_have_text('Keep the first match in the interval.')
            page.locator('#workspace').get_by_role('button', name='Dismiss annotation').click()
            playwright.expect(page.locator('#workspace .annotation')).to_have_count(0)
            playwright.expect(editor).to_have_text('Keep the first match in the interval.')
            page.locator('[data-tool=visualization]').click()
            player=page.locator('.visual-player')
            playwright.expect(player).to_contain_text('Step 1 of 4')
            player.get_by_role('button',name='Next').click()
            playwright.expect(player).to_contain_text('Interval [0, 2)')
            player.get_by_role('button',name='Previous').click()
            playwright.expect(player).to_contain_text('Step 1 of 4')
            player.get_by_label('Speed').select_option('2')
            player.get_by_role('button',name='Play',exact=True).click()
            playwright.expect(player).to_contain_text('Step 4 of 4')
            player.get_by_role('button',name='Restart').click()
            playwright.expect(player).to_contain_text('Step 1 of 4')
            page.locator('#chat').fill('How should I update right?')
            page.get_by_role('button',name='Send',exact=True).click()
            playwright.expect(page.locator('.student-message')).to_contain_text('How should I update right?')
            old=page.locator('#problem').inner_text()
            editor.fill('1');page.locator('#submit').click()
            playwright.expect(page.locator('#problem')).not_to_have_text(old)
            playwright.expect(page.locator('#mastery')).to_contain_text('50%')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.get_by_role('button', name='End session').click()
            playwright.expect(page.get_by_text('Session ended.', exact=False).last).to_be_visible()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors,errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
