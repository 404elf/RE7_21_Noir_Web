"""Real WebSockets with simulated 200ms each-way delay, no engine shortcuts."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'.artifacts'
DELAY = """(() => {
  window.latencyWire = {actions:0, results:[], feedback:[], samples:[], lastTurnAt:0};
  window.WebSocket = new Proxy(window.WebSocket, {construct(Target,args) {
    const ws=Reflect.construct(Target,args), send=ws.send.bind(ws);
    let handler, active=0, lastSubmission;
    ws.send=raw => {
      const d=JSON.parse(raw);
      if(d.type==='action') {
        window.latencyWire.actions++;
        lastSubmission={at:performance.now(),id:d.command_id};
        queueMicrotask(()=>{
          const card=document.querySelector('.trump-card[data-pending-action]');
          window.latencyWire.feedback.push({ms:performance.now()-lastSubmission.at,
            animation:card ? getComputedStyle(card).animationName : null,
            busy:document.getElementById(d.action==='TRUMP'?'use-trump':d.action==='DISCARD'?'discard-trump':d.action==='HIT'?'hit':'stay').getAttribute('aria-busy'),
            confirming:document.getElementById('turn-hint').textContent});
        });
      }
      setTimeout(()=>{if(ws.readyState===1)send(raw);},200);
    };
    Object.defineProperty(ws,'onmessage',{get:()=>handler,set:callback=>{
      handler=callback;
      ws.addEventListener('message',event=>setTimeout(()=>{
        const d=JSON.parse(event.data), now=performance.now();
        if(d.type==='network')window.latencyWire.samples.push(d);
        const timing=d.timing || (d.type==='clock' ? d : null);
        if(timing && timing.clock_active!==active) {
          active=timing.clock_active;
          if(active===1)window.latencyWire.lastTurnAt=now;
        }
        if(d.action_result)window.latencyWire.results.push({...d.action_result,
          send_to_confirm_ms:lastSubmission?.id===d.action_result.command_id ? now-lastSubmission.at : null});
        callback(event);
      },200));
    }});
    return ws;
  }});
})();"""


def check_latency(url):
    report, errors = {}, []
    expect.set_options(timeout=15000)
    with sync_playwright() as p:
        browser=p.chromium.launch()
        pages=[browser.new_page(viewport={'width':1440,'height':900}) for _ in range(2)]
        try:
            a,b=pages
            for page in pages:
                page.add_init_script(DELAY)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
                page.goto(url+'/')
            config=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
            config['game_settings'].update(max_hp=2,initial_trumps_count=8,
                                           round_reward_trumps_count=0,number_card_draw_probability=0)
            config['trump_weights']={k:0 for k,v in config['trump_weights'].items() if isinstance(v,(int,float))}
            config['trump_weights']['Add 1']=1
            created=a.request.post(url+'/api/rooms',data={'name':'计时体验测试','config':config,
                'timer':dict(enabled=True,mode='turn',turn_seconds=5,preparation_seconds=60,settlement_seconds=.2)})
            assert created.ok,created.text()
            host=created.json()
            joined=b.request.post(url+f"/api/rooms/{host['room']}/join",data={'name':'计时对手'})
            assert joined.ok,joined.text()
            prefix=urlsplit(url).path.rstrip('/')
            key=prefix+'/noir-seat' if prefix else 'noir-seat'
            for page,seat in [(a,host),(b,joined.json())]:
                page.evaluate('([key,seat])=>sessionStorage.setItem(key,JSON.stringify(seat))',[key,seat])
                page.goto(url+'/?room='+seat['room'])
                expect(page.locator('#ready')).to_be_enabled()
                page.wait_for_function('() => window.latencyWire.samples.length>0')
            a.locator('#ready').click()
            expect(b.locator('#lobby-players')).to_contain_text('已准备')
            b.locator('#ready').click()
            expect(a.locator('#game')).to_be_visible()
            expect(a.locator('#stay')).to_be_enabled()
            for index,action in enumerate(['TRUMP','TRUMP','DISCARD']):
                a.locator('.trump-card').first.click()
                button='#use-trump' if action=='TRUMP' else '#discard-trump'
                expect(a.locator(button)).to_be_enabled()
                a.locator(button).click()
                expect(a.locator('.trump-card.pending')).to_have_count(1)
                expect(a.locator('#clock-1')).to_contain_text('确认')
                expect(a.locator('#trump-count')).to_contain_text(f'{7-index} /')
            wire=a.evaluate('window.latencyWire')
            assert wire['actions']==3
            assert all(f['ms']<100 and f['busy']=='true' and '正在确认' not in f['confirming'] for f in wire['feedback'])
            assert [f['animation'] for f in wire['feedback']]==['submit-up','submit-up','submit-right']
            results=[r for r in wire['results'] if r['send_to_confirm_ms'] is not None]
            assert len(results)==3
            assert all(0 <= r['compensated_ms'] <= 500 and r['processing_ms'] >= 0 for r in results)
            assert 0 < sum(r['compensated_ms'] for r in results) <= 1000.01
            report['consecutive_trumps']=wire
            print('PASS: immediate local card feedback and bounded clock refunds over real delayed sockets',flush=True)

            # Hand off twice to get a fresh turn and quota, without resetting rules.
            expect(a.locator('#hit')).to_be_enabled();a.locator('#hit').click()
            expect(b.locator('#hit')).to_be_enabled();b.locator('#hit').click()
            expect(a.locator('#stay')).to_be_enabled()
            old_actions=a.evaluate('window.latencyWire.actions')
            a.evaluate("""() => new Promise(resolve => {
              const delay=Math.max(0,window.latencyWire.lastTurnAt+4700-performance.now());
              setTimeout(()=>{document.getElementById('stay').click();resolve();},delay);
            })""")
            expect(b.locator('#stay')).to_be_enabled()
            assert a.evaluate('window.latencyWire.actions') == old_actions+1
            latest=a.evaluate('window.latencyWire.results.filter(r=>r.send_to_confirm_ms!==null).at(-1)')
            assert latest['compensated_ms']>0
            assert not a.locator('#events').inner_text().count('计时耗尽')
            report['near_deadline']=latest
            print('PASS: a move made locally before the deadline is accepted after crossing the server deadline within measured grace',flush=True)
            b.locator('#stay').click()
            a.reload()
            expect(a.locator('#game')).to_be_visible()
            a.wait_for_function('() => window.latencyWire.samples.length>0')
            expect(a.locator('#connection')).to_contain_text('ms')
            report['refresh_reconnect']='authenticated connection and latency estimate restored'
            assert not errors,errors
            print('PASS: refresh reconnect and server-measured latency badge; no browser errors',flush=True)
        finally:
            report['errors']=errors
            (OUT/'web-latency-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            for index,page in enumerate(pages):
                if not page.is_closed():page.screenshot(path=str(OUT/f'web-latency-{index}.png'),animations='disabled')
            browser.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-path',default='/re7')
    parser.add_argument('--url')
    args=parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.url:
        check_latency(args.url.rstrip('/'))
        return
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
    env=dict(os.environ,NOIR_ORIGIN='',NOIR_BASE_PATH=args.base_path)
    log_path=OUT/'web-latency-server.log'
    with log_path.open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','web.app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            url=f'http://127.0.0.1:{port}'+args.base_path.rstrip('/')
            for _ in range(100):
                try:
                    urllib.request.urlopen(url+'/healthz',timeout=.5).close();break
                except OSError:
                    if process.poll() is not None:raise RuntimeError(log_path.read_text(encoding='utf-8'))
                    time.sleep(.1)
            else:raise RuntimeError('Latency acceptance server did not start')
            check_latency(url)
        finally:
            process.terminate();process.wait(timeout=10)


if __name__=='__main__':
    main()
