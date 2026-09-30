// Result grouping is independent of rendering, transport, and ATS mode names.
export function buildResultOverview(run){
  const tasks=new Map(), rounds=new Map(), scenarioCounts=new Map(), issuesByIndex=new Map();
  const indices=new Set(run.results.map((result,index)=>result.index??index));
  const unlinkedIssues=[];
  for(const issue of run.issues){
    if(issue.result_index===null||issue.result_index===undefined||!indices.has(issue.result_index)){
      unlinkedIssues.push(issue);continue;
    }
    if(!issuesByIndex.has(issue.result_index))issuesByIndex.set(issue.result_index,[]);
    issuesByIndex.get(issue.result_index).push(issue);
  }
  const scenarioNames=new Set(run.results.map(result=>result.scenario||run.mode.name||run.mode.id));
  run.results.forEach((result,index)=>{
    const scenario=result.scenario||run.mode.name||run.mode.id;
    const cycle=Number.isInteger(result.cycle)&&result.cycle>0?result.cycle:null;
    const roundKey=JSON.stringify([scenario,cycle]);
    const taskKey=JSON.stringify([scenario,result.module||'',result.name||'']);
    if(!tasks.has(taskKey)){
      const number=(scenarioCounts.get(scenario)||0)+1;scenarioCounts.set(scenario,number);
      tasks.set(taskKey,{id:String(tasks.size),number,name:result.name||result.module||'未命名用例',module:result.module||'',scenario});
    }
    const task=tasks.get(taskKey);
    if(!rounds.has(roundKey)){
      const label=cycle===null?'未标注轮次':`第 ${cycle} 轮`;
      rounds.set(roundKey,{id:String(rounds.size),cycle,scenario,label:scenarioNames.size>1?`${scenario} · ${label}`:label,records:[],tasks:new Map(),attention:[]});
    }
    const round=rounds.get(roundKey), issues=issuesByIndex.get(result.index??index)||[];
    const record={result,task,issues,position:index+1,attention:!['PASS','SKIP'].includes(result.status)||issues.length>0};
    round.records.push(record);
    if(!round.tasks.has(task.id))round.tasks.set(task.id,{task,records:[],attention:[]});
    const cell=round.tasks.get(task.id);cell.records.push(record);
    if(record.attention){round.attention.push(record);cell.attention.push(record);}
  });
  return {rounds:[...rounds.values()],tasks:[...tasks.values()],unlinkedIssues};
}

export function newResultView(model){
  return {onlyIssues:model.rounds.some(round=>round.attention.length),cycle:'',page:1,selected:'',task:'',detailOnlyIssues:true,detailPage:1};
}

export function renderResultOverview(model,view,{esc,badge,duration,empty,hasFiles}){
  const PAGE_SIZE=20, DETAIL_SIZE=25;
  const attentionRounds=model.rounds.filter(round=>round.attention.length);
  const filtered=model.rounds.filter(round=>(!view.onlyIssues||round.attention.length)&&(!view.cycle||round.id===view.cycle));
  const pages=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE));
  view.page=Math.max(1,Math.min(view.page,pages));
  const visible=filtered.slice((view.page-1)*PAGE_SIZE,view.page*PAGE_SIZE);
  const repLabel=record=>record.result.rep>0?`第 ${record.result.rep} 次`:'次数未标注';
  const issueKinds=record=>[...new Set(record.issues.map(issue=>issue.category_name))];
  const attentionItem=(round,cell)=>{
    const repeats=[...new Set(cell.attention.map(repLabel))];
    const labels=cell.attention.some(record=>['FAIL','ERROR'].includes(record.result.status))?'失败 / 错误':cell.attention.some(record=>!['PASS','SKIP'].includes(record.result.status))?'待确认':'检测到问题';
    return `<button class="result-attention-item" data-action="result-task" data-round="${round.id}" data-task="${cell.task.id}"><b>第 ${cell.task.number} 项 · ${esc(cell.task.name)}</b><small>${esc(repeats.slice(0,3).join('、'))}${repeats.length>3?` 等 ${repeats.length} 个次数`:''} · ${labels}</small></button>`;
  };
  const roundSummary=round=>{
    const failed=round.records.filter(record=>['FAIL','ERROR'].includes(record.result.status)).length;
    const skipped=round.records.filter(record=>record.result.status==='SKIP').length;
    return `${round.tasks.size} 个测试项 · ${round.records.length} 条记录${failed?` · ${failed} 条失败 / 错误`:''}${skipped?` · ${skipped} 条跳过`:''}`;
  };
  const navigation=(kind,page,count,total)=>count>1?`<div class="pagination"><span>共 ${total} ${kind==='round'?'组轮次':'条记录'}</span><div class="pagination-controls"><button class="button" data-action="${kind}-prev" ${page<=1?'disabled':''}>上一页</button><span>${page} / ${count}</span><button class="button" data-action="${kind}-next" ${page>=count?'disabled':''}>下一页</button></div></div>`:'';
  const selected=model.rounds.find(round=>round.id===view.selected);
  function details(){
    if(!selected)return '';
    const task=model.tasks.find(task=>task.id===view.task);
    const records=selected.records.filter(record=>(!task||record.task.id===task.id)&&(!view.detailOnlyIssues||record.attention));
    const count=Math.max(1,Math.ceil(records.length/DETAIL_SIZE));view.detailPage=Math.max(1,Math.min(view.detailPage,count));
    return `<section id="result-round-detail" class="result-round-detail" tabindex="-1" aria-label="本轮测试详情"><div class="result-detail-heading"><div><h3>${esc(selected.label)}${task?` · 第 ${task.number} 项 ${esc(task.name)}`:''}</h3><p>${roundSummary(selected)}</p></div><button class="button" data-action="result-close">收起本轮详情</button></div><div class="result-detail-controls"><label class="checkbox"><input id="result-detail-only" type="checkbox" ${view.detailOnlyIssues?'checked':''}>仅看本轮问题记录</label>${task?'<button class="button link" data-action="result-all-tasks">查看本轮所有测试项</button>':''}<span>${records.length} 条符合条件的记录</span></div>${records.length?`<div class="table-scroll"><table class="results-table result-records"><thead><tr><th>测试项 / 原始记录</th><th>重复次数</th><th>原始判定</th><th>问题 / 日志</th><th>耗时</th></tr></thead><tbody>${records.slice((view.detailPage-1)*DETAIL_SIZE,view.detailPage*DETAIL_SIZE).map(record=>{
      const r=record.result, issue=record.issues.find(issue=>issue.search||issue.artifact)||record.issues[0];
      return `<tr class="result-record-row"><td><span class="result-name">第 ${record.task.number} 项 · ${esc(record.task.name)}</span><small class="result-module">${esc(record.task.module||'模块未标注')} · 原报告第 ${record.position} 条</small><details class="case-detail"><summary>完整记录</summary><pre>${esc([r.timestamp?`时间：${r.timestamp}`:'',r.message?`说明：${r.message}`:'',r.detail?`详情：\n${r.detail}`:''].filter(Boolean).join('\n')||'没有附加说明')}</pre></details></td><td>${repLabel(record)}</td><td>${badge(r.status)}</td><td>${issueKinds(record).map(label=>`<span class="result-problem-tag">${esc(label)}</span>`).join('')||`<span class="muted">${record.attention?'判定需关注':'无已识别问题'}</span>`}${hasFiles&&record.attention?`<button class="button link" data-action="result-log" data-index="${record.result.index}">查看日志</button>`:''}</td><td>${duration(r.elapsed_ms)}</td></tr>`;
    }).join('')}</tbody></table></div>`:empty('没有符合条件的记录','可取消“仅看本轮问题记录”查看其他执行结果。')}${navigation('record',view.detailPage,count,records.length)}</section>`;
  }
  return `<section class="result-overview" aria-label="按轮次查看用例结果"><div class="result-overview-heading"><div><h2>轮次总览</h2><p>先定位哪一轮、哪一项有问题，再展开执行记录。</p></div><span class="result-overview-status ${attentionRounds.length?'has-attention':''}">${attentionRounds.length?`${attentionRounds.length} 组轮次需关注`:'用例记录无已识别问题'}</span></div><div class="result-overview-stats"><span>已记录轮次 <b>${model.rounds.filter(round=>round.cycle!==null).length}</b></span><span>测试项 <b>${model.tasks.length}</b></span><span>需关注记录 <b>${attentionRounds.reduce((total,round)=>total+round.attention.length,0)}</b></span>${model.rounds.some(round=>round.cycle===null)?'<span>含未标注轮次的记录</span>':''}</div><div class="result-overview-controls"><label class="checkbox"><input id="result-only-issues" type="checkbox" ${view.onlyIssues?'checked':''}>仅看有问题的轮次</label><label class="result-cycle-label">选择轮次<select class="text-input" id="result-cycle"><option value="">全部轮次</option>${model.rounds.map(round=>`<option value="${round.id}" ${view.cycle===round.id?'selected':''}>${esc(round.label)}</option>`).join('')}</select></label></div>${model.unlinkedIssues.length?`<div class="result-unlinked">另有 ${model.unlinkedIssues.length} 条问题未关联到具体测试项。<button class="button link" data-tab="issues">查看问题记录</button></div>`:''}${visible.length?`<div class="table-scroll"><table class="result-rounds"><thead><tr><th>轮次</th><th>执行记录</th><th>有问题的测试项</th><th></th></tr></thead><tbody>${visible.map(round=>{
    const affected=[...round.tasks.values()].filter(cell=>cell.attention.length);
    return `<tr class="result-round-row ${round.attention.length?'has-attention':''}"><td><strong>${esc(round.label)}</strong></td><td>${roundSummary(round)}</td><td><div class="result-attention-items">${affected.slice(0,4).map(cell=>attentionItem(round,cell)).join('')||'<span class="result-clear">无已识别问题</span>'}${affected.length>4?`<button class="button link" data-action="result-round" data-round="${round.id}">另 ${affected.length-4} 项，查看本轮</button>`:''}</div></td><td><button class="button" data-action="result-round" data-round="${round.id}">查看本轮</button></td></tr>`;
  }).join('')}</tbody></table></div>`:empty('没有符合条件的轮次','取消“仅看有问题的轮次”，或选择其他轮次。')}${navigation('round',view.page,pages,filtered.length)}<p class="result-numbering-note">编号按每个场景中测试项首次出现的顺序生成；同模块、同名称的重复执行合并展示。编号不代表 YAML 中的序号，未上报的项不推定为通过。</p>${details()}</section>`;
}
