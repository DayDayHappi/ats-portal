// Run against tests/seed_demo.py in a disposable data directory.
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
await page.goto((process.env.PORTAL_TEST_URL||'http://127.0.0.1:8010')+'/#runs/qa-demo-20260928-001');
await page.getByRole('tab',{name:/原始日志/}).click();
await page.locator('.log-line').first().waitFor();
assert.equal(await page.getByRole('button',{name:'下一段'}).count(),0,'Raw log should not require paging buttons');
assert.ok(await page.locator('#log-content').evaluate(e=>e.clientHeight)>=600,'Default reading area should be taller');
const range=()=>page.locator('.line-number').evaluateAll(nodes=>[Number(nodes[0]?.textContent),Number(nodes.at(-1)?.textContent)]);
const waitReady=()=>page.waitForFunction(()=>document.querySelector('#log-content')?.getAttribute('aria-busy')==='false');
const scrollAndAnchor=toBottom=>page.locator('#log-content').evaluate((e,down)=>{
  e.scrollTop=down?e.scrollHeight:0;
  const top=e.getBoundingClientRect().top;
  const row=[...e.querySelectorAll('.log-line')].find(r=>r.getBoundingClientRect().bottom>top);
  return {number:row.querySelector('.line-number').textContent,offset:row.getBoundingClientRect().top-top};
},toBottom);
const checkAnchor=async anchor=>{
  const offset=await page.locator('#log-content').evaluate((e,number)=>{
    const row=[...e.querySelectorAll('.log-line')].find(r=>r.querySelector('.line-number').textContent===number);
    return row?.getBoundingClientRect().top-e.getBoundingClientRect().top;
  },anchor.number);
  assert.ok(Math.abs(offset-anchor.offset)<1,'Fetching rows must keep the visible line in place');
};
await waitReady();
assert.deepEqual(await range(),[1,300]);
// Grow across three fetch boundaries, then verify DOM stays bounded.
for(const last of [600,900,1003]){
  const anchor=await scrollAndAnchor(true);
  await page.waitForFunction(n=>Number([...document.querySelectorAll('.line-number')].at(-1)?.textContent)===n,last);
  await waitReady();
  await checkAnchor(anchor);
}
assert.ok(await page.locator('.log-line').count()<=900);
assert.ok((await range())[0]>1);
// Scrolling upward reloads removed earlier lines without a manual page action.
const previousAnchor=await scrollAndAnchor(false);
await page.waitForFunction(()=>document.querySelector('.line-number')?.textContent==='1');
await waitReady();
await checkAnchor(previousAnchor);
assert.equal(await page.evaluate(()=>window.__xss),undefined);
await page.getByRole('button',{name:'展开阅读',exact:true}).click();
const bounds=await page.locator('.log-workspace').boundingBox();
assert.ok(bounds.width>1350&&bounds.height>900);
await page.screenshot({path:process.env.PORTAL_SCROLL_SCREENSHOT||'/tmp/ats-scroll-expanded.png',fullPage:true});
await page.keyboard.press('Escape');
assert.equal(await page.getByRole('button',{name:'展开阅读',exact:true}).count(),1);
assert.equal(await page.evaluate(()=>document.body.classList.contains('log-expanded')),false);
await page.getByLabel('跳转行号').fill('701');
await page.getByRole('button',{name:'跳转',exact:true}).click();
await page.locator('.log-line.target').waitFor();
assert.equal(await page.locator('.log-line.target .line-number').textContent(),'701');
// A transient paging failure must keep already-read content and allow retry.
let failOnce=true;
await page.route('**/artifacts/serial.log/lines?**',async route=>{
  if(failOnce&&new URL(route.request().url()).searchParams.get('start')==='1001'){
    failOnce=false;return route.fulfill({status:503,contentType:'application/json',body:'{"detail":"temporary failure"}'});
  }
  return route.continue();
});
await waitReady();
await page.locator('#log-content').evaluate(e=>{e.scrollTop=e.scrollHeight;});
await page.getByRole('button',{name:'重试加载',exact:true}).waitFor();
assert.equal((await range())[0],701);
await page.getByRole('button',{name:'重试加载',exact:true}).click();
await page.waitForFunction(()=>Number([...document.querySelectorAll('.line-number')].at(-1)?.textContent)===1003);
await page.getByLabel('日志搜索关键词').fill('TT ERROR');
await page.getByRole('button',{name:'搜索',exact:true}).click();
await page.getByRole('button',{name:'第 2 行：查看上下文',exact:true}).click();
await page.locator('.log-line.target').waitFor();
assert.equal(await page.locator('.log-line.target .line-number').textContent(),'2');
await page.getByRole('button',{name:/^run.log/}).click();
await page.locator('.log-line').first().waitFor();
assert.ok(await page.locator('#log-content').textContent().then(t=>t.includes('SYNTHETIC QA FIXTURE')));
assert.equal((await range())[0],1);
// A response from the previous file must not overwrite the newly selected file.
let releaseDelayed,markRequested;
const released=new Promise(resolve=>{releaseDelayed=resolve;});
const requested=new Promise(resolve=>{markRequested=resolve;});
await page.route('**/artifacts/serial.log/lines?start=301&limit=300',async route=>{
  const response=await route.fetch();markRequested();await released;
  await route.fulfill({response});
});
await page.getByRole('button',{name:/^serial.log/}).click();
await waitReady();
await scrollAndAnchor(true);
await requested;
await page.getByRole('button',{name:/^run.log/}).click();
await waitReady();
const lateResponse=page.waitForResponse(r=>r.url().includes('/serial.log/lines?start=301'));
releaseDelayed();await lateResponse;
await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
assert.ok((await page.locator('#log-content').textContent()).includes('SYNTHETIC QA FIXTURE'));
assert.deepEqual(await range(),[1,2]);
await page.setViewportSize({width:390,height:844});
await page.getByRole('button',{name:'展开阅读',exact:true}).click();
assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
await page.getByRole('button',{name:'收起阅读',exact:true}).click();
assert.deepEqual(errors,[]);
console.log('Continuous log reading PASS: down/up, stable reading position, bounded DOM, expanded desktop/mobile, jump/search, failure/retry, stale file response.');
}finally{await browser.close();}
