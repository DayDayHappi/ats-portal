'use strict';
import {buildResultOverview,newResultView,renderResultOverview} from './results.js';

const $ = (s, root=document) => root.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function logText(text){
  const raw=String(text??'');
  // ATS prefixes each physical send line with [timestamp] TX>.
  const tx=/^(\s*(?:\[[^\]\r\n]*\]\s*)?)(TX>.*)$/.exec(raw);
  return tx?`${esc(tx[1])}<strong class="tx-command">${esc(tx[2])}</strong>`:esc(raw);
}
const LOG_BATCH_SIZE=300;
const LOG_WINDOW_SIZE=900;
const newLog = (values={}) => ({name:'',start:1,next:1,query:'',search:false,hasMore:false,history:[],lines:[],loading:false,loadError:null,lastScroll:0,target:null,locate:null,...values});
const state = {filters:{mode:'',date_from:'',date_to:'',status:'',category:'',label:'',q:'',issues_only:false,unannotated:false}, page:1, pageSize:15, options:{modes:[],categories:[],labels:[],timezone:'Asia/Shanghai',ai_enabled:false,ai_model:''}, current:null, tab:'results', issuePage:1, log:newLog(), analysis:null, trends:null, version:0, logVersion:0};
let listVersion=0;
const main = $('#main');
const statusLabels = {PASS:'通过',FAIL:'失败',ERROR:'执行错误',SKIP:'跳过',ABORTED:'已中止',UNKNOWN:'待确认'};
const fmt = (value, dateOnly=false) => {
  if(!value) return '未记录';
  const d=new Date(value);
  if(Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN',{timeZone:state.options.timezone,year:'numeric',month:'2-digit',day:'2-digit',...(dateOnly?{}:{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})}).format(d);
};
const bytes = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(1)} MB`;
const duration = ms => ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms/1000).toFixed(1)} 秒` : `${Math.floor(ms/60000)} 分 ${Math.floor(ms/1000)%60} 秒`;
const empty = (title,description='') => `<div class="empty"><div class="empty-symbol">▤</div><h3>${esc(title)}</h3><p>${esc(description)}</p></div>`;
const errorHtml = message => `<div class="error-box" role="alert">${esc(message)} <button class="button link" data-action="refresh">重新加载</button></div>`;

// Filter/page persistence (localStorage). Issues-only follows the nav state, not persisted.
const FILTER_KEY = 'ats-portal:filters';
const FILTER_FIELDS = ['mode', 'date_from', 'date_to', 'status', 'category', 'label', 'q'];
function saveFilters(){
  try{
    const {issues_only, ...rest} = state.filters;
    localStorage.setItem(FILTER_KEY, JSON.stringify({...rest, page: state.page}));
  }catch{}
}
function loadFilters(){
  try{
    const saved = JSON.parse(localStorage.getItem(FILTER_KEY) || 'null');
    if(saved && typeof saved === 'object'){
      for(const key of FILTER_FIELDS) if(typeof saved[key] === 'string') state.filters[key] = saved[key];
      if(Number.isInteger(saved.page) && saved.page > 0) state.page = saved.page;
    }
  }catch{}
}
let toastTimer;
function showToast(message){
  const toast = $('#toast');
  if(!toast) return;
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 1800);
}
const skeleton = (h, w='100%') => `<div class="skeleton" style="height:${h};width:${w}"></div>`;
const listSkeleton = () => `<div style="padding:12px 23px">${'<div class="skeleton" style="height:54px;margin:6px 0"></div>'.repeat(6)}</div>`;

async function api(path, init={}) {
  const response = await fetch(`/api/v1${path}`,init);
  let data;
  try {data=await response.json();} catch {throw new Error('服务返回了无法识别的数据，请检查本机服务。');}
  if(response.status===401){
    if(!$('#auth-dialog').open) $('#auth-dialog').showModal();
    throw new Error(data.detail || '需要访问凭据');
  }
  if(!response.ok) throw new Error(typeof data.detail==='string' ? data.detail : '请求参数不正确，请检查筛选条件。');
  return data;
}

function badge(status, receiving=false){
  const key=receiving?'receiving':Object.hasOwn(statusLabels,status)?status.toLowerCase():'unknown';
  return `<span class="badge ${key}">${esc(receiving?'接收中':statusLabels[status]||status)}${!receiving&&statusLabels[status]?` · ${esc(status)}`:''}</span>`;
}

function setNav(issues){
  $('#nav-runs').classList.toggle('active',!issues);
  $('#nav-issues').classList.toggle('active',issues);
}

async function route(){
  setLogExpanded(false);
  const version=++state.version;
  state.logVersion++;
  const hash=location.hash || '#runs';
  setNav(hash==='#issues');
  try{
    state.options=await api('/options');
    if(version!==state.version) return;
    $('#timezone-label').textContent=state.options.timezone;
    if(hash.startsWith('#runs/')){
      main.innerHTML='<div class="loading-detail">'+skeleton('30px','240px')+skeleton('26px','180px')+skeleton('120px','100%')+'</div>';
      const run=await api(`/runs/${encodeURIComponent(decodeURIComponent(hash.slice(6)))}`);
      if(version!==state.version) return;
      if(state.current?.id!==run.id)state.log=newLog();
      state.current=run;state.tab='results';state.issuePage=1;state.analysis=null;
      state.resultOverview=buildResultOverview(run);state.resultView=newResultView(state.resultOverview);
      renderDetail();
    }else{
      state.current=null;
      if(hash==='#issues') state.filters.issues_only=true;
      loadFilters();
      renderList();
      await loadList();
    }
  }catch(error){if(version===state.version) main.innerHTML=errorHtml(error.message);}
}

function options(items, selected, all){return `<option value="">${all}</option>`+items.map(item=>`<option value="${esc(item.id)}" ${item.id===selected?'selected':''}>${esc(item.name)}${item.count!==undefined?` (${item.count})`:''}</option>`).join('');}

function renderList(){
  const f=state.filters;
  main.innerHTML=`<div class="page-heading"><div><div class="eyebrow">TEST EXECUTIONS</div><h1>${location.hash==='#issues'?'问题视图':'测试执行'}</h1><p>${location.hash==='#issues'?'查看检测到异常的运行，保留原始测试判定。':'按日期与压测模式，查看每一次测试。'}</p></div><span class="date-chip">${esc(fmt(new Date().toISOString(),true))}</span></div>
  <section class="metrics" id="metrics" aria-label="当前筛选结果统计"></section>
  ${state.options.ai_enabled?`<section class="panel" id="trends-host"><div class="list-heading"><h2>跨运行趋势分析</h2><span class="muted">基于当前筛选范围</span><button class="button soft" data-action="trends">AI 趋势分析</button></div></section>`:''}
  <section class="panel"><form class="filters" id="filter-form">
    <div class="field date"><label for="date-from">开始日期</label><input id="date-from" type="date" name="date_from" value="${esc(f.date_from)}"></div>
    <div class="field date"><label for="date-to">结束日期</label><input id="date-to" type="date" name="date_to" value="${esc(f.date_to)}"></div>
    <div class="field mode"><label for="mode-filter">压测模式</label><select id="mode-filter" name="mode">${options(state.options.modes,f.mode,'全部模式')}</select></div>
    <div class="field"><label for="status-filter">测试结果</label><select id="status-filter" name="status">${options(Object.entries(statusLabels).map(([id,name])=>({id,name})),f.status,'全部结果')}</select></div>
    <div class="field"><label for="category-filter">问题分类</label><select id="category-filter" name="category">${options(state.options.categories,f.category,'全部分类')}</select></div>
    <div class="field"><label for="label-filter">根因标签</label><select id="label-filter" name="label">${options(state.options.labels,f.label,'全部标签')}</select></div>
    <div class="filter-row"><div class="field search"><input type="search" name="q" value="${esc(f.q)}" placeholder="搜索模式或运行编号" aria-label="搜索模式或运行编号"></div><button class="button soft" type="submit">筛选</button><label class="checkbox"><input name="issues_only" type="checkbox" ${f.issues_only?'checked':''}>仅看有问题的运行</label><label class="checkbox"><input name="unannotated" type="checkbox" ${f.unannotated?'checked':''}>仅看未标注</label><button class="button link" type="button" data-action="reset">重置筛选</button><span class="filter-summary">按测试开始日期归档</span></div>
  </form><div class="list-heading"><h2>运行记录 <span id="total-count" class="counter">—</span></h2><span class="muted">最近执行优先</span></div><div id="run-list">${listSkeleton()}</div></section>`;
  renderMetrics({runs:'—',passed:'—',with_issues:'—',issues:'—',complete:0},true);
}

function renderMetrics(s,loading=false){
  $('#metrics').innerHTML=[['运行总数',s.runs,'', '▤',loading?'正在读取':'当前筛选范围'],['通过的运行',s.passed,'green','✓','保留 ATS 原始判定'],['有问题的运行',s.with_issues,'red','◇',`${s.issues} 条问题记录`],['待接收完成',loading?'—':s.runs-s.complete,'purple','◷',`${s.complete} 次运行已归档`]].map(([label,value,color,glyph,note])=>`<article class="metric ${color}"><div class="metric-label">${label}<span class="metric-glyph" aria-hidden="true">${glyph}</span></div><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join('');
}

function progress(s){return `<div class="progress" aria-label="通过 ${s.passed}，失败 ${s.failed+s.errored}，跳过 ${s.skipped}"><span class="ok" data-width="${s.total?s.passed/s.total*100:0}"></span><span class="bad" data-width="${s.total?(s.failed+s.errored)/s.total*100:0}"></span><span class="skip" data-width="${s.total?s.skipped/s.total*100:0}"></span></div><div class="results-numbers"><span class="ok">${s.passed} 通过</span><span class="bad">${s.failed+s.errored} 异常</span><span>/ ${s.total}</span></div>`;}

async function loadList(){
  const version=++listVersion;
  const params=new URLSearchParams({page:state.page,page_size:state.pageSize,...Object.fromEntries(Object.entries(state.filters).filter(([,v])=>v!==''))});
  try{
    const data=await api(`/runs?${params}`);
    if(version!==listVersion||state.current||!$('#run-list'))return;
    renderMetrics(data.stats);
    $('#total-count').textContent=data.total;
    if(!data.items.length){$('#run-list').innerHTML=empty(data.total?'这一页没有记录':'没有符合条件的运行',state.options.modes.length?'调整日期或筛选条件后再试。':'ATS 完成测试并上报后，记录会出现在这里。');return;}
    $('#run-list').innerHTML=`<div class="table-scroll"><table class="runs-table"><thead><tr><th>压测模式 / 运行编号</th><th>开始时间</th><th>测试结果</th><th>用例结果</th><th>问题记录</th><th aria-label="查看详情"></th></tr></thead><tbody>${data.items.map(run=>`<tr><td><a class="run-title" href="#runs/${encodeURIComponent(run.id)}">${esc(run.mode.name)}</a><span class="run-id">${esc(run.id)}</span></td><td class="time-cell">${esc(fmt(run.started_at).split(' ')[0])}<small>${esc(fmt(run.started_at).split(' ').slice(1).join(' '))}</small></td><td>${badge(run.status,run.upload_state!=='complete')}</td><td>${progress(run.summary)}</td><td>${run.issue_count?`<span class="issue-count">${run.issue_count} 条问题</span><span class="issue-labels">${esc(run.categories.map(c=>c.name).slice(0,2).join(' · '))}</span>`:'<span class="no-issues">—</span>'}${run.attention_count>run.annotated_count?`<span class="pending-badge">${run.attention_count-run.annotated_count} 待标注</span>`:''}</td><td><a href="#runs/${encodeURIComponent(run.id)}" class="arrow-link" aria-label="查看 ${esc(run.mode.name)} 详情">›</a></td></tr>`).join('')}</tbody></table></div><div class="pagination"><span>共 ${data.total} 次运行 · 每页 ${state.pageSize} 条</span><div class="pagination-controls"><button class="button" data-action="prev-page" ${data.page===1?'disabled':''}>上一页</button><span>${data.page} / ${Math.max(1,Math.ceil(data.total/state.pageSize))}</span><button class="button" data-action="next-page" ${data.page*state.pageSize>=data.total?'disabled':''}>下一页</button></div></div>`;
    document.querySelectorAll('[data-width]').forEach(e=>e.style.width=`${Number(e.dataset.width)||0}%`);
  }catch(error){if(version===listVersion&&$('#run-list'))$('#run-list').innerHTML=errorHtml(error.message);}
}

function renderDetail(){
  const r=state.current;
  const wall=r.ended_at?duration(new Date(r.ended_at)-new Date(r.started_at)):'未记录结束时间';
  main.innerHTML=`<a href="#runs" class="back-link" aria-label="返回测试记录">‹ 返回测试记录</a><div class="page-heading detail-heading"><div><div class="eyebrow">EXECUTION DETAILS</div><h1>${esc(r.mode.name)}</h1><span class="run-id">${esc(r.id)}</span><button class="copy-run-id button link" data-action="copy-run-id" title="复制运行编号" aria-label="复制运行编号">⧉</button></div>${badge(r.status,r.upload_state!=='complete')}</div><dl class="panel detail-meta">${[['开始时间',fmt(r.started_at)],['结束时间',fmt(r.ended_at)],['运行时长',wall],['执行主机',r.source.host||'未记录'],['设备 / 固件',renderMeta(r)],['归档日期',r.run_date],['收到时间',fmt(r.received_at)],['资料状态',r.upload_state==='complete'?'已完整归档':'等待文件接收 / 归档确认']].map(([k,v])=>`<div class="meta-item"><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl><section class="panel"><div class="detail-total"><span>用例总数<b>${r.summary.total}</b></span><span>通过<b>${r.summary.passed}</b></span><span>失败 / 错误<b>${r.summary.failed+r.summary.errored}</b></span><span>跳过<b>${r.summary.skipped}</b></span><span>问题记录<b>${r.issue_count}</b></span><span>需关注<b>${r.attention_count??'—'}</b></span><span>已标注<b>${(r.annotations||[]).length}</b></span></div><div class="tabs" role="tablist" aria-label="运行详情"><button role="tab" data-tab="results">用例结果 (${r.results.length})</button><button role="tab" data-tab="issues">问题 (${r.issues.length})</button><button role="tab" data-tab="logs">原始日志 (${r.artifacts.length})</button>${state.options.ai_enabled?'<button role="tab" data-tab="analysis">AI 分析</button>':''}</div><div class="tab-content" id="tab-content" role="tabpanel"></div></section>`;
  renderTab();
}

function renderMeta(r){
  const m=r.meta;
  const device=m?.device||r.source.device;
  const firmware=m?.firmware||r.source.firmware;
  const shown=[device,firmware].filter(Boolean).join(' / ')||'未记录';
  const edit=r.upload_state==='complete'?`<button class="button link meta-edit" data-action="edit-meta" title="编辑设备 / 固件" aria-label="编辑设备 / 固件">✎</button>`:'';
  const by=m?`<small class="meta-by">${esc(m.author||'未署名')} · ${esc(fmt(m.updated_at))}</small>`:'';
  return `<span class="meta-value">${esc(shown)}${edit}</span>${by}`;
}

function renderTab(){
  state.logVersion++;
  if(state.tab!=='logs')setLogExpanded(false);
  document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===state.tab);b.setAttribute('aria-selected',String(b.dataset.tab===state.tab));});
  if(state.tab==='logs')return renderLogs();
  if(state.tab==='issues')return renderIssues();
  if(state.tab==='analysis')return renderAnalysisTab();
  const r=state.current;
  if(!r.results.length){$('#tab-content').innerHTML=empty('没有可展示的用例结果',r.upload_state==='complete'?'本次运行没有提供有效的用例结果，运行状态保留为待确认或已中止。':'上传完成后，用例结果将显示在这里。');return;}
  renderResults();
}

function renderResults(){
  $('#tab-content').innerHTML=renderResultOverview(state.resultOverview,state.resultView,{esc,badge,duration,empty,hasFiles:state.current.artifacts.some(file=>file.received)});
}

function detailPagination(kind,page,total,size){return `<div class="pagination"><span>共 ${total} 条 · 原始记录保留完整</span><div class="pagination-controls"><button class="button" data-action="${kind}-prev" ${page<=1?'disabled':''}>上一页</button><span>${page} / ${Math.max(1,Math.ceil(total/size))}</span><button class="button" data-action="${kind}-next" ${page*size>=total?'disabled':''}>下一页</button></div></div>`;}

function renderIssues(){
  const r=state.current;
  if(!r.issues.length){$('#tab-content').innerHTML=empty('暂无已识别的问题','问题标记独立于测试判定；可继续查看原始日志。');return;}
  $('#tab-content').innerHTML=`<div class="tab-toolbar"><span>问题标记不会覆盖 ATS 的测试结果</span><span>规则：${esc(r.rules_version||'—')}</span></div><div class="issue-list">${r.issues.slice((state.issuePage-1)*50,state.issuePage*50).map(issue=>`<article class="issue-card"><div class="issue-card-top"><span class="issue-category">${esc(issue.category_name)}</span><span class="issue-location">${esc(issue.module)}${issue.cycle!==null?` · C${esc(issue.cycle)} / R${esc(issue.rep)}`:''}</span></div><h3>${esc(issue.title)}</h3>${issue.evidence?`<pre>${esc(issue.evidence)}</pre>`:''}${r.artifacts.some(a=>a.received)?`<div class="issue-actions"><button class="button soft" data-action="issue-log" data-id="${issue.id}">${issue.search?'在日志中搜索':'查看原始日志'}</button></div>`:''}</article>`).join('')}</div>${detailPagination('issue',state.issuePage,r.issues.length,50)}`;
}

function artifactBase(){return `/runs/${encodeURIComponent(state.current.id)}/artifacts/${encodeURIComponent(state.log.name)}`;}
function renderAnalysisTab(){
  const a=state.analysis;
  if(!a){
    $('#tab-content').innerHTML=`<div class="analysis-panel"><div class="analysis-placeholder"><div class="empty-symbol">✦</div><h3>AI 根因分析</h3><p>AI 将对比本次运行的异常日志片段与同一测试项在成功运行中的日志，推断可能的根因。仅在点击时调用 AI 服务，日志片段会发送到所配置的 AI 接口。</p><button class="button primary" data-action="run-analysis">生成分析</button></div></div>`;
    return;
  }
  $('#tab-content').innerHTML=renderAnalysis(a,'run');
}
function renderAnalysis(data, kind){
  const a=data.analysis||{summary:'',findings:[],recurring:[]};
  const meta=`<span class="analysis-meta">${esc(data.model||'')} · ${data.cached?'缓存结果':esc(fmt(data.created_at))}</span>`;
  const refresh=`<button class="button soft" data-action="${kind==='run'?'run-analysis-refresh':'trends-refresh'}">重新分析</button>`;
  const body=kind==='run'
    ? (a.findings||[]).map(f=>`<article class="analysis-finding"><div class="analysis-finding-top"><b>${esc(f.test_item||'')}</b>${f.cycle!==undefined&&f.cycle!==null&&f.cycle!==''?`<span class="muted">第${esc(f.cycle)}轮</span>`:''}${f.status?`${badge(f.status)}`:''}</div><p class="analysis-cause">${esc(f.likely_root_cause||'')}</p>${f.evidence?`<pre>${esc(f.evidence)}</pre>`:''}${f.confidence?`<span class="analysis-confidence">置信度 ${esc(f.confidence)}</span>`:''}${f.suggested_action?`<p class="analysis-action">建议：${esc(f.suggested_action)}</p>`:''}</article>`).join('')
    : (a.recurring||[]).map(f=>`<article class="analysis-finding"><div class="analysis-finding-top"><b>${esc(f.pattern||'')}</b>${f.test_item?`<span class="muted">${esc(f.test_item)}</span>`:''}</div>${f.occurrence_count?`<span class="analysis-confidence">出现 ${esc(f.occurrence_count)} 次</span>`:''}${f.affected_runs?`<span class="analysis-confidence">影响 ${esc(f.affected_runs)} 次运行</span>`:''}${f.hypothesis?`<p class="analysis-cause">${esc(f.hypothesis)}</p>`:''}${f.suggested_action?`<p class="analysis-action">建议：${esc(f.suggested_action)}</p>`:''}</article>`).join('');
  return `<div class="analysis-panel"><div class="tab-toolbar"><span>AI 分析仅供参考，需结合人工判断</span>${meta}</div><div class="analysis-summary"><h3>总体结论</h3><p>${esc(a.summary||'')}</p></div><div class="analysis-actions">${refresh}</div>${body||'<p class="muted">没有可展示的分析条目。</p>'}</div>`;
}
async function analyzeRun(refresh){
  const box=$('#tab-content');
  box.innerHTML=`<div class="analysis-panel"><div class="analysis-loading">${skeleton('18px','60%')}<p class="muted">正在生成分析，可能需要数十秒…</p></div></div>`;
  try{
    const data=await api(`/runs/${encodeURIComponent(state.current.id)}/analysis`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh})});
    state.analysis=data;renderAnalysisTab();
  }catch(error){box.innerHTML=errorHtml(error.message);}
}
async function analyzeTrends(refresh){
  const box=$('#trends-host');
  box.innerHTML=`<div class="analysis-panel"><div class="analysis-loading">${skeleton('18px','60%')}<p class="muted">正在汇总跨运行趋势…</p></div></div>`;
  try{
    const data=await api('/analysis/trends',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:state.filters.mode,date_from:state.filters.date_from,date_to:state.filters.date_to,refresh})});
    state.trends=data;renderTrends();
  }catch(error){box.innerHTML=errorHtml(error.message);}
}
function renderTrends(){
  const host=$('#trends-host');
  if(!host)return;
  if(!state.trends){host.innerHTML=`<section class="panel"><div class="list-heading"><h2>跨运行趋势分析</h2><span class="muted">基于当前筛选范围</span></div><div class="analysis-panel"><div class="analysis-placeholder"><div class="empty-symbol">✦</div><h3>AI 趋势分析</h3><p>汇总当前筛选范围内多次运行的失败模式，找出反复出现的规律。</p><button class="button primary" data-action="trends">生成趋势分析</button></div></div></section>`;return;}
  host.innerHTML=`<section class="panel"><div class="list-heading"><h2>跨运行趋势分析</h2><span class="muted">基于当前筛选范围</span></div>${renderAnalysis(state.trends,'trends')}</section>`;
}
function logFile(){
  const files=state.current.artifacts.filter(a=>a.received);
  return (files.find(a=>a.name==='serial.log')||files.find(a=>a.kind==='log')||files[0]);
}
function locateTime(timestamp, elapsed_ms){
  // Start wall-clock of a result: record time minus its duration. Logs carry only
  // [HH:MM:SS.mmm], so return both that wall-clock and how many midnights the
  // start sits past the run's own started_at (logs have no date, but a midnight
  // wrap makes the naive wall-clock comparison point at the wrong day).
  if(!timestamp)return null;
  const match=/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$/.exec(String(timestamp));
  if(!match)return null;
  const [,y,mo,d,h,mi,s,frac]=match;
  const startMs=Date.UTC(+y,+mo-1,+d,+h,+mi,+s)+(frac?Number(frac.padEnd(3,'0')):0)-Number(elapsed_ms||0);
  if(!Number.isFinite(startMs))return null;
  const dt=new Date(startMs);
  const pad=n=>String(n).padStart(2,'0');
  const at=`${pad(dt.getUTCHours())}:${pad(dt.getUTCMinutes())}:${pad(dt.getUTCSeconds())}.${String(dt.getUTCMilliseconds()).padStart(3,'0')}`;
  return {at, day:dayOffset(startMs)};
}
function dayOffset(ms){
  // Whole days between an instant and the run's start, both in the server timezone.
  const started=state.current?.started_at;
  if(!started)return 0;
  const tz=state.options.timezone||'Asia/Shanghai';
  const dayNumber=value=>{
    const parts=new Intl.DateTimeFormat('en-US',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(value));
    const [yy,mm,dd]=['year','month','day'].map(t=>+parts.find(p=>p.type===t).value);
    return Math.floor(Date.UTC(yy,mm-1,dd)/86400000);
  };
  return dayNumber(ms)-dayNumber(new Date(started).getTime());
}
function openResultLog(artifactName, search, result){
  // Priority: keyword search (a rule hit) > wall-clock locate > name search.
  const file=logFile();
  const name=file?.name||'';
  const searchIssue=result&&state.current.issues.find(i=>i.result_index===result.index&&i.search);
  const query=search||searchIssue?.search||'';
  if(query)return void(state.log=newLog({name,query,search:true}));
  const locate=result&&locateTime(result.timestamp,result.elapsed_ms);
  if(locate)return void(state.log=newLog({name,locate}));
  const byName=result?.name||artifactName||'';
  state.log=newLog({name,query:byName,search:Boolean(byName)});
}
function renderLogs(){
  const files=state.current.artifacts;
  if(!files.some(a=>a.received)){$('#tab-content').innerHTML=empty('原始文件尚未接收','请等待脚本上传完成后刷新。');return;}
  if(!files.some(a=>a.name===state.log.name&&a.received))state.log.name=(files.find(a=>a.name==='serial.log'&&a.received)||files.find(a=>a.kind==='log'&&a.received)||files.find(a=>a.received)).name;
  $('#tab-content').innerHTML=`<div class="log-workspace"><aside class="file-list" aria-label="本次运行文件"><h3>FILES / 原始文件</h3>${files.map(file=>`<button class="file-button ${file.name===state.log.name?'active':''}" data-action="file" data-name="${esc(file.name)}" ${!file.received?'disabled':''}>${esc(file.name)}<small>${bytes(file.size)}${file.received?'':' · 未收到'}</small></button>`).join('')}</aside><section class="log-main"><div class="log-toolbar"><strong>${esc(state.log.name)}</strong><div class="log-toolbar-actions"><button class="button soft" data-action="expand-log" aria-pressed="false">展开阅读</button><a class="button" href="/api/v1${artifactBase()}/download">↓ 下载原文件</a></div></div><form id="log-search" class="log-controls"><input class="text-input" name="q" aria-label="日志搜索关键词" placeholder="搜索关键词，如 TT ERROR" value="${esc(state.log.query)}" maxlength="200"><button class="button soft" type="submit">搜索</button><input class="text-input line-input" type="number" min="1" name="line" aria-label="跳转行号" placeholder="行号"><button class="button" type="button" data-action="goto-line">跳转</button></form><div id="log-content" class="log-content" tabindex="0" aria-label="日志阅读区"></div><div class="log-bottom"><span id="log-range" role="status">滚动阅读 · 原始文件未修改</span><button class="button" data-action="retry-log" hidden>重试加载</button><div id="log-search-pages" hidden><button class="button" data-action="log-prev">上一段</button> <button class="button" data-action="log-next">下一段</button></div></div></section></div>`;
  setLogExpanded(Boolean(state.logExpanded));
  const content=$('#log-content');
  content.addEventListener('scroll',()=>{
    const l=state.log, top=content.scrollTop, down=top>l.lastScroll, up=top<l.lastScroll;
    l.lastScroll=top;
    if(l.search||l.loading||l.loadError)return;
    if(down&&top+content.clientHeight>=content.scrollHeight-200&&l.hasMore)loadRawLog('next');
    else if(up&&top<200&&l.lines[0]?.number>1)loadRawLog('previous');
  },{passive:true});
  // At the very top after a line jump, an upward wheel/key has no scroll event.
  const readPrevious=()=>{
    const l=state.log;
    if(!l.search&&!l.loading&&!l.loadError&&content.scrollTop===0&&l.lines[0]?.number>1)loadRawLog('previous');
  };
  content.addEventListener('wheel',event=>{if(event.deltaY<0)readPrevious();},{passive:true});
  content.addEventListener('keydown',event=>{if(['ArrowUp','PageUp'].includes(event.key))readPrevious();});
  loadLog();
}

function setLogExpanded(expanded){
  state.logExpanded=expanded;
  document.body.classList.toggle('log-expanded',expanded);
  $('.log-workspace')?.classList.toggle('is-expanded',expanded);
  const button=$('[data-action="expand-log"]');
  if(button){button.textContent=expanded?'收起阅读':'展开阅读';button.setAttribute('aria-pressed',String(expanded));}
}

function rawLineHtml(line,target){
  return `<div class="log-line ${line.number===target?'target':/\b(error|fail|panic)\b|超时/i.test(line.text)?'is-error':''}"><span class="line-number">${line.number}</span><span class="line-text">${logText(line.text)}</span>${line.truncated?'<span class="line-truncated">… 长行显示已截断，下载可查看完整内容</span>':''}</div>`;
}

function updateLogRange(){
  const l=state.log, range=$('#log-range');
  if(!range)return;
  const position=l.lines.length?`第 ${l.lines[0].number}–${l.lines.at(-1).number} 行`:'此处没有内容';
  range.textContent=`${position} · ${l.loading?'正在加载…':l.loadError?`加载失败：${l.loadError.message}`:l.hasMore?'向下滚动继续阅读':'已到文件末尾'}${l.lines[0]?.number>1?' · 可向上滚动查看前文':''}`;
  $('[data-action="retry-log"]').hidden=!l.loadError;
}

async function loadRawLog(direction='reset'){
  const l=state.log, token=state.logVersion, content=$('#log-content');
  if(!content||l.search||l.loading)return;
  const first=l.lines[0]?.number||l.start;
  const start=direction==='previous'?Math.max(1,first-LOG_BATCH_SIZE):direction==='next'?l.next:l.start;
  const limit=direction==='previous'?first-start:LOG_BATCH_SIZE;
  if(!limit)return;
  const current=()=>token===state.logVersion&&l===state.log&&content===$('#log-content');
  l.loading=true;l.loadError=null;content.setAttribute('aria-busy','true');updateLogRange();
  try{
    const data=await api(`${artifactBase()}/lines?${new URLSearchParams({start,limit})}`);
    if(!current())return;
    // Preserve the visible row while bounding both retained text and DOM nodes.
    // Earlier rows are fetched again when the reader scrolls back up.
    const oldRows=[...content.querySelectorAll('.log-line')];
    const viewportTop=content.getBoundingClientRect().top;
    const anchor=direction==='reset'?null:oldRows.find(row=>row.getBoundingClientRect().bottom>viewportTop);
    const anchorNumber=anchor?Number($('.line-number',anchor).textContent):null;
    const anchorTop=anchor?.getBoundingClientRect().top;
    const oldScroll=content.scrollTop, oldLeft=content.scrollLeft;
    const combined=direction==='previous'?[...data.lines,...l.lines]:direction==='next'?[...l.lines,...data.lines]:data.lines;
    let windowStart=direction==='next'?Math.max(0,combined.length-LOG_WINDOW_SIZE):0;
    const anchorIndex=combined.findIndex(line=>line.number===anchorNumber);
    if(anchorIndex>=0)windowStart=Math.max(Math.min(windowStart,anchorIndex),anchorIndex-LOG_WINDOW_SIZE+1);
    l.lines=combined.slice(windowStart,windowStart+LOG_WINDOW_SIZE);
    const removedTail=l.lines.at(-1)?.number<combined.at(-1)?.number;
    l.hasMore=(direction==='previous'?l.hasMore:data.has_more)||removedTail;
    l.next=l.lines.length?l.lines.at(-1).number+1:data.next_start;
    content.innerHTML=l.lines.length?l.lines.map(line=>rawLineHtml(line,l.target)).join(''):'<div class="log-message">此处没有内容，可输入行号 1 返回文件开头。</div>';
    if(direction==='reset')content.scrollTop=0;
    else{
      const nextAnchor=anchorIndex>=0?content.querySelectorAll('.log-line')[anchorIndex-windowStart]:null;
      content.scrollTop=nextAnchor?content.scrollTop+nextAnchor.getBoundingClientRect().top-anchorTop:oldScroll;
    }
    content.scrollLeft=direction==='reset'?0:oldLeft;
    l.lastScroll=content.scrollTop;
  }catch(error){
    if(!current())return;
    l.loadError={direction,message:error.message};
    if(!l.lines.length)content.innerHTML='<div class="log-message">日志暂时无法读取，请点击下方的“重试加载”。</div>';
  }finally{
    if(current()){l.loading=false;content.setAttribute('aria-busy','false');updateLogRange();}
  }
}

async function loadLog(target=null){
  const token=++state.logVersion;
  const l=state.log;
  const base=artifactBase();
  l.lines=[];l.loading=false;l.loadError=null;l.lastScroll=0;l.target=target;
  $('#log-content').innerHTML='<div class="log-message">正在读取…</div>';
  $('#log-content').setAttribute('aria-busy','true');
  $('#log-search-pages').hidden=!l.search;
  $('[data-action="retry-log"]').hidden=true;
  $('[data-action="log-prev"]').disabled=true;
  $('[data-action="log-next"]').disabled=true;
  if(l.locate){
    // Resolve a wall-clock time to a line number, then jump like a line target.
    const locate=l.locate;
    l.locate=null;
    try{
      const params={at:locate.at};
      if(locate.day)params.day=locate.day;
      const data=await api(`${base}/locate?${new URLSearchParams(params)}`);
      if(token===state.logVersion&&$('#log-content')){
        // Land a few lines before the located TX command so context is visible.
        l.start=Math.max(1,data.number-10);
        return loadLog(data.number);
      }
    }catch(error){
      if(token!==state.logVersion||!$('#log-content'))return;
      $('#log-content').innerHTML=`<div class="log-message">${esc(error.message)}，已显示文件开头。</div>`;
    }
    return loadRawLog();
  }
  if(!l.search)return loadRawLog();
  const query=new URLSearchParams({start:l.start,limit:50,q:l.query});
  try{
    const data=await api(`${base}/search?${query}`);
    if(token!==state.logVersion||!$('#log-content'))return;
    l.next=data.next_start;l.hasMore=data.has_more;
    $('#log-content').innerHTML=data.matches.length?data.matches.map(line=>`<button class="search-result" data-action="context" data-line="${line.number}" aria-label="第 ${line.number} 行：查看上下文"><span>第 ${line.number} 行</span><span>${logText(line.text)}${line.truncated?' …（长行已折叠，匹配可能位于隐藏部分）':''}</span></button>`).join(''):`<div class="log-message">整个文件都没有找到匹配。${l.history.length?'可点击“上一段”返回上一批结果。':''}</div>`;
    $('#log-range').textContent=`找到 ${data.matches.length} 行${data.matches.length>=50?'（仅显示前 50 条）':''} · ${data.has_more?'还有更多匹配，点击“下一段”继续':'已搜索到文件末尾'}`;
    $('[data-action="log-prev"]').disabled=!l.history.length;
    $('[data-action="log-next"]').disabled=!l.hasMore;
    $('#log-content').scrollTop=0;
  }catch(error){if(token===state.logVersion&&$('#log-content'))$('#log-content').innerHTML=`<div class="log-message error-text">${esc(error.message)}</div>`;}
  finally{if(token===state.logVersion&&$('#log-content'))$('#log-content').setAttribute('aria-busy','false');}
}

function readFilters(){
  const form=$('#filter-form');
  for(const key of Object.keys(state.filters))state.filters[key]=key==='issues_only'||key==='unannotated'?form.elements[key].checked:form.elements[key].value;
  state.page=1;
  saveFilters();
}

// ---- annotation dialog ----
function currentAnnotation(index){
  return (state.current?.annotations||[]).find(a=>a.result_index===index)||null;
}
function openAnnotationDialog(index){
  const result=state.current?.results?.[index];
  if(!result)return;
  const dialog=$('#annotation-dialog');
  const ann=currentAnnotation(index);
  const selected=new Set(ann?ann.labels.map(l=>l.id):[]);
  dialog._index=index;
  $('#annotation-result-title').textContent=result.name||`原报告第 ${index+1} 条`;
  $('#annotation-result-sub').textContent=`${result.module||'模块未标注'} · 原报告第 ${index+1} 条`;
  $('#annotation-note').value=ann?.note||'';
  $('#annotation-author').value=ann?.author||(localStorage.getItem('ats-portal:author')||'');
  $('#annotation-error').textContent='';
  $('#annotation-delete').hidden=!ann;
  renderAnnotationLabels(selected);
  dialog.showModal();
}
function renderAnnotationLabels(selected){
  const box=$('#annotation-labels');
  box.innerHTML=state.options.labels.map(l=>`<label class="annotation-label-option"><input type="checkbox" name="label" value="${esc(l.id)}" ${selected.has(l.id)?'checked':''}>${esc(l.name)}<small>${l.count||''}</small></label>`).join('')||'<span class="muted">暂无标签，请在下方新增。</span>';
}
function selectedLabelIds(){
  return [...document.querySelectorAll('#annotation-labels input[name="label"]:checked')].map(el=>el.value);
}
async function refreshAnnotations(){
  const run=state.current;
  if(!run)return;
  run.annotations=await api(`/runs/${encodeURIComponent(run.id)}/annotations`);
  state.resultOverview=buildResultOverview(run);state.resultView=newResultView(state.resultOverview);
  renderTab();
}
async function saveAnnotation(){
  const index=$('#annotation-dialog')._index;
  const labelIds=selectedLabelIds();
  const note=$('#annotation-note').value;
  const author=$('#annotation-author').value;
  const err=$('#annotation-error');
  if(!labelIds.length){err.textContent='请至少选择一个标签';return;}
  localStorage.setItem('ats-portal:author',author);
  try{
    await api(`/runs/${encodeURIComponent(state.current.id)}/results/${index}/annotation`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({label_ids:labelIds,note,author})});
    $('#annotation-dialog').close();
    await refreshAnnotations();
  }catch(error){err.textContent=error.message;}
}
async function deleteAnnotation(){
  const index=$('#annotation-dialog')._index;
  try{
    await api(`/runs/${encodeURIComponent(state.current.id)}/results/${index}/annotation`,{method:'DELETE'});
    $('#annotation-dialog').close();
    await refreshAnnotations();
  }catch(error){$('#annotation-error').textContent=error.message;}
}
async function addLabelInline(){
  const input=$('#annotation-new-label');
  const name=input.value.trim();
  const err=$('#annotation-error');
  if(!name){err.textContent='请输入标签名称';return;}
  try{
    const label=await api('/labels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
    state.options.labels=[...state.options.labels.filter(l=>l.id!==label.id),label];
    input.value='';
    const selected=new Set(selectedLabelIds());
    selected.add(label.id);
    renderAnnotationLabels(selected);
  }catch(error){err.textContent=error.message;}
}

// ---- device / firmware meta dialog ----
function openMetaDialog(){
  const r=state.current;
  const m=r.meta;
  $('#meta-device').value=m?.device||r.source.device||'';
  $('#meta-firmware').value=m?.firmware||r.source.firmware||'';
  $('#meta-author').value=m?.author||(localStorage.getItem('ats-portal:author')||'');
  $('#meta-error').textContent='';
  $('#meta-dialog').showModal();
}
async function saveMeta(){
  const err=$('#meta-error');
  const device=$('#meta-device').value.trim();
  const firmware=$('#meta-firmware').value.trim();
  const author=$('#meta-author').value.trim();
  localStorage.setItem('ats-portal:author',author);
  try{
    const meta=await api(`/runs/${encodeURIComponent(state.current.id)}/meta`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({device,firmware,author})});
    state.current.meta=meta;
    $('#meta-dialog').close();
    renderDetail();
  }catch(error){err.textContent=error.message;}
}

main.addEventListener('submit',event=>{
  event.preventDefault();
  if(event.target.id==='filter-form'){readFilters();loadList();}
  if(event.target.id==='log-search'){
    state.log.query=event.target.elements.q.value.trim();state.log.start=1;state.log.history=[];state.log.search=Boolean(state.log.query);loadLog();
  }
});
main.addEventListener('change',event=>{
  if(event.target.closest('#filter-form')&&event.target.name!=='q'){readFilters();loadList();}
  if(event.target.id==='result-only-issues'){state.resultView.onlyIssues=event.target.checked;state.resultView.page=1;renderResults();}
  if(event.target.id==='result-cycle'){state.resultView.cycle=event.target.value;state.resultView.page=1;state.resultView.selected='';renderResults();}
  if(event.target.id==='result-detail-only'){state.resultView.detailOnlyIssues=event.target.checked;state.resultView.detailPage=1;renderResults();}
});
main.addEventListener('click',event=>{
  const tab=event.target.closest('[data-tab]');
  if(tab){state.tab=tab.dataset.tab;renderTab();return;}
  const button=event.target.closest('[data-action]');
  if(!button||button.disabled)return;
  const action=button.dataset.action;
  if(action==='refresh')return route();
  if(action==='reset'){
    for(const k of Object.keys(state.filters))state.filters[k]=k==='issues_only'||k==='unannotated'?(k==='issues_only'&&location.hash==='#issues'):'';
    state.page=1;saveFilters();renderList();return loadList();
  }
  if(action==='prev-page'||action==='next-page'){state.page+=action==='prev-page'?-1:1;saveFilters();return loadList();}
  if(action==='copy-run-id'){
    if(navigator.clipboard?.writeText){navigator.clipboard.writeText(state.current.id).then(()=>showToast('运行编号已复制'),()=>showToast('复制失败，请手动选择'));}
    else showToast(state.current.id);
    return;
  }
  if(['issue-prev','issue-next'].includes(action)){
    state.issuePage+=action.endsWith('prev')?-1:1;return renderTab();
  }
  if(['round-prev','round-next','record-prev','record-next'].includes(action)){
    state.resultView[action.startsWith('round')?'page':'detailPage']+=action.endsWith('prev')?-1:1;return renderResults();
  }
  if(action==='result-round'||action==='result-task'){
    const round=state.resultOverview.rounds.find(round=>round.id===button.dataset.round);
    if(!round)return;
    Object.assign(state.resultView,{selected:round.id,task:button.dataset.task||'',detailPage:1,detailOnlyIssues:Boolean(round.attention.length)});
    renderResults();$('#result-round-detail').scrollIntoView({block:'start'});$('#result-round-detail').focus({preventScroll:true});return;
  }
  if(action==='result-close'){state.resultView.selected='';renderResults();return;}
  if(action==='result-all-tasks'){state.resultView.task='';state.resultView.detailPage=1;renderResults();return;}
  if(action==='run-analysis'){state.tab='analysis';renderTab();return analyzeRun(false);}
  if(action==='run-analysis-refresh')return analyzeRun(true);
  if(action==='trends')return analyzeTrends(false);
  if(action==='trends-refresh')return analyzeTrends(true);
  if(action==='file'){state.log=newLog({name:button.dataset.name});return renderLogs();}
  if(action==='expand-log'){setLogExpanded(!state.logExpanded);return;}
  if(action==='retry-log')return loadRawLog(state.log.loadError?.direction||'reset');
  if(action==='issue-log'){
    const issue=state.current.issues.find(i=>i.id===Number(button.dataset.id));
    const result=issue&&issue.result_index!==null&&issue.result_index!==undefined?state.current.results[issue.result_index]:null;
    openResultLog(issue?.artifact||'', issue?.search, result);
    state.tab='logs';return renderTab();
  }
  if(action==='result-log'){
    const result=state.current.results[Number(button.dataset.index)];
    openResultLog('', '', result);
    state.tab='logs';return renderTab();
  }
  if(action==='annotate'){openAnnotationDialog(Number(button.dataset.index));return;}
  if(action==='edit-meta'){openMetaDialog();return;}
  if(action==='log-next'){state.log.history.push(state.log.start);state.log.start=state.log.next;return loadLog();}
  if(action==='log-prev'){state.log.start=state.log.history.pop()??1;return loadLog();}
  if(action==='context'){const line=Number(button.dataset.line);state.log.history=[];state.log.search=false;state.log.start=Math.max(1,line-10);return loadLog(line);}
  if(action==='goto-line'){
    const line=Number($('#log-search').elements.line.value);
    if(!Number.isInteger(line)||line<1)return;
    state.log.history=[];state.log.search=false;state.log.start=line;return loadLog(line);
  }
});
document.addEventListener('keydown',event=>{
  if(event.key==='/'){
    const el=document.activeElement, tag=el?.tagName;
    if(!$('#auth-dialog')?.open && tag!=='INPUT' && tag!=='TEXTAREA' && tag!=='SELECT' && !el?.isContentEditable){
      const search=$('#filter-form')?.elements.q;
      if(search){event.preventDefault();search.focus();}
    }
    return;
  }
  if(!state.logExpanded||$('#auth-dialog').open)return;
  if(event.key==='Escape'){setLogExpanded(false);$('[data-action="expand-log"]')?.focus();}
  if(event.key==='Tab'){
    const controls=[...$('.log-workspace').querySelectorAll('button:not(:disabled),a[href],input,[tabindex="0"]')].filter(e=>e.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(!$('.log-workspace').contains(document.activeElement)){event.preventDefault();first?.focus();}
    else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
});
$('#refresh').addEventListener('click',route);
$('#nav-runs').addEventListener('click',()=>{state.filters.issues_only=false;state.page=1;if(location.hash==='#runs')route();});
$('#nav-issues').addEventListener('click',()=>{state.filters.issues_only=true;state.page=1;if(location.hash==='#issues')route();});
$('#auth-form').addEventListener('submit',async event=>{
  event.preventDefault();
  try{
    await api('/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:event.target.elements.token.value})});
    event.target.reset();$('#auth-error').textContent='';$('#auth-dialog').close();route();
  }catch(error){$('#auth-error').textContent=error.message;}
});
$('#annotation-save').addEventListener('click',saveAnnotation);
$('#annotation-delete').addEventListener('click',deleteAnnotation);
$('#annotation-add-label').addEventListener('click',addLabelInline);
$('[data-action="annotation-cancel"]').addEventListener('click',()=>$('#annotation-dialog').close());
$('#meta-save').addEventListener('click',saveMeta);
$('[data-action="meta-cancel"]').addEventListener('click',()=>$('#meta-dialog').close());
$('#annotation-new-label').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();addLabelInline();}});
window.addEventListener('hashchange',route);
route();
