// Navigation regressions, using the disposable seed_demo dataset.
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
const url=process.env.PORTAL_TEST_URL||'http://127.0.0.1:8010';
const failures=[];
async function check(name,test){
  const page=await browser.newPage();
  try{await test(page);console.log('PASS '+name);}catch(e){failures.push(name+': '+e.message);}
  finally{await page.close();}
}
async function logs(page,id){
  await page.goto(url+'/#runs/qa-demo-20260928-'+id);
  await page.getByRole('tab',{name:/原始日志/}).click();
  await page.locator('.log-line').first().waitFor();
}
await check('Switching runs resets log offset and query',async page=>{
  await logs(page,'001');
  await page.getByLabel('跳转行号').fill('301');
  await page.getByRole('button',{name:'跳转',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.log-line.target .line-number')?.textContent==='301');
  await page.getByRole('link',{name:'返回测试记录'}).click();
  await page.locator('a.run-title[href="#runs/qa-demo-20260928-002"]').click();
  await page.getByRole('tab',{name:/原始日志/}).click();
  await page.locator('.log-line').first().waitFor();
  assert.equal(await page.locator('.line-number').first().textContent(),'1');
  assert.equal(await page.getByLabel('日志搜索关键词').inputValue(),'');
});
await check('Search Previous restores actual cursor',async page=>{
  await logs(page,'001');
  await page.getByLabel('日志搜索关键词').fill('capture progress');
  await page.getByRole('button',{name:'搜索',exact:true}).click();
  await page.getByRole('button',{name:'第 4 行：查看上下文',exact:true}).waitFor();
  await page.getByRole('button',{name:'下一段'}).click();
  await page.getByRole('button',{name:'第 54 行：查看上下文',exact:true}).waitFor();
  await page.getByRole('button',{name:'下一段'}).click();
  await page.getByRole('button',{name:'第 104 行：查看上下文',exact:true}).waitFor();
  await page.getByRole('button',{name:'上一段'}).click();
  await page.locator('.search-result').first().waitFor();
  assert.match(await page.locator('.search-result').first().getAttribute('aria-label'),/^第 54 行/);
});
await browser.close();
assert.deepEqual(failures,[]);
