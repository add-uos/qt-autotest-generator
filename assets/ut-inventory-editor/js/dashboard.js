// ═══ dashboard.js — 多项目看板 ═══
function normDashProject(p){const st=p.stats||{};return{name:p.name||p.project||p.github,mcpName:p.mcp_name||p.mcpName||'',size:p.size||'?',branch:p.branch||'master',branchSource:p.branch_source||'fallback',generatedAt:p.generated_at||p.generatedAt||'',baseSha:p.base_sha||p.baseSha||'',stats:{testable:st.testable??st.total_methods??0,high:st.high??0,mid:st.mid??0,low:st.low??0,reviewPending:st.review_pending??0,withCover:st.with_test_cover??st.withCover??0,noCoverHigh:st.no_cover_high??0,noCoverMid:st.no_cover_mid??0},topGap:p.top_gap||p.topGap||[]}}
function dashBranchFor(name){const short=(name||'').replace(/^home-uos-service-codebase-repos-/,'');const p=(S.dash.projects||[]).find(x=>(x.name||'')===short||(x.name||'')===name);return p&&p.branch?p.branch:null}
function saveDashCache(){try{localStorage.setItem('utie-dash-projects',JSON.stringify(S.dash.projects))}catch(e){}}
function saveDashSnapshot(){if(!S.dash.projects.length)return;const t=S.dash.projects.reduce((a,p)=>({testable:a.testable+p.stats.testable,high:a.high+p.stats.high,noCoverHigh:a.noCoverHigh+p.stats.noCoverHigh,noCoverMid:a.noCoverMid+p.stats.noCoverMid,withCover:a.withCover+p.stats.withCover}),{testable:0,high:0,noCoverHigh:0,noCoverMid:0,withCover:0});let h=[];try{h=JSON.parse(localStorage.getItem('utie-dash-history')||'[]')}catch(e){}const now=Date.now();if(h.length&&now-h[h.length-1].ts<120000){h[h.length-1]={ts:now,...t}}else h.push({ts:now,...t});if(h.length>200)h=h.slice(-200);try{localStorage.setItem('utie-dash-history',JSON.stringify(h))}catch(e){}}
function initDashDropdown(wrapId,btnId,ddId,labelId,onPick){const wrap=$(wrapId),dd=$(ddId);const items=()=>[...dd.querySelectorAll('.export-dropdown-item')];const sync=()=>{const v=wrap.dataset.value||'';items().forEach(it=>it.classList.toggle('selected',it.dataset.val===v));$(labelId).textContent=(items().find(it=>it.dataset.val===v)||{textContent:''}).textContent};$(btnId).onclick=e=>{e.stopPropagation();$$('.export-dropdown.show').forEach(d=>{if(d!==dd)d.classList.remove('show')});dd.classList.toggle('show')};items().forEach(it=>{it.onclick=e=>{e.stopPropagation();wrap.dataset.value=it.dataset.val;dd.classList.remove('show');sync();onPick(it.dataset.val)}});sync()}
function dashFiltered(){const q=($('#dash-search').value||'').toLowerCase(),sz=$('#dash-size-wrap').dataset.value||'',gap=$('#dash-onlygap').checked,sk=$('#dash-sort-wrap').dataset.value||'noCoverHigh';let l=S.dash.projects;if(q)l=l.filter(p=>p.name.toLowerCase().includes(q));if(sz)l=l.filter(p=>p.size===sz);if(gap)l=l.filter(p=>p.stats.noCoverHigh>0);const cmp={noCoverHigh:(a,b)=>b.stats.noCoverHigh-a.stats.noCoverHigh,noCoverMid:(a,b)=>b.stats.noCoverMid-a.stats.noCoverMid,high:(a,b)=>b.stats.high-a.stats.high,testable:(a,b)=>b.stats.testable-a.stats.testable,name:(a,b)=>a.name.localeCompare(b.name)}[sk]||((a,b)=>b.stats.noCoverHigh-a.stats.noCoverHigh);return[...l].sort(cmp)}
function renderDash(){const sub=S.dash.sub;$$('.dash-subtab').forEach(t=>t.classList.toggle('active',t.dataset.dsub===sub));$('#dash-overview').classList.toggle('hidden',sub!=='overview');$('#dash-kanban').classList.toggle('hidden',sub!=='kanban');$('#dash-trend').classList.toggle('hidden',sub!=='trend');renderDashStats();if(sub==='overview')renderDashGrid();else if(sub==='kanban')renderKanban();else renderTrend();try{lucide.createIcons()}catch(e){}}
function renderDashStats(){const t=S.dash.projects.reduce((a,p)=>({testable:a.testable+p.stats.testable,high:a.high+p.stats.high,mid:a.mid+p.stats.mid,review:a.review+p.stats.reviewPending,gapH:a.gapH+p.stats.noCoverHigh,gapM:a.gapM+p.stats.noCoverMid,cover:a.cover+p.stats.withCover}),{testable:0,high:0,mid:0,review:0,gapH:0,gapM:0,cover:0});const cov=t.testable?Math.round(t.cover/t.testable*100):0;$('#dash-stats').innerHTML=[[t.testable,'总可测方法','var(--text)'],[t.high,'🟢 高优','var(--accent)'],[t.review,'⚠ 待复核','var(--warn)'],[t.gapH+t.gapM,'高/中优无覆盖','var(--danger,#ef4444)'],[cov+'%','测试覆盖率','var(--info)']].map(([v,l,c])=>`<div class="stat-card"><div class="v" style="color:${c}">${v}</div><div class="l">${l}</div></div>`).join('')}
function renderDashGrid(){const l=dashFiltered();$('#dash-count').textContent=l.length+' 项目';const el=$('#dash-overview');if(!l.length){el.innerHTML='<div class="dash-empty" style="grid-column:1/-1"><i data-lucide="layout-grid" class="icon"></i><div style="margin-top:8px">无项目数据 — 点击右上「刷新」或「导入」</div></div>';return}el.innerHTML=l.map(p=>{const s=p.stats,cov=s.testable?Math.round(s.withCover/s.testable*100):0,gapCls=s.noCoverHigh>0?'risk':'ok',hm=s.high+s.mid;return`<div class="dash-card" data-proj="${p.name}"><div class="dash-card-header"><span class="size-badge ${(p.size||'').toLowerCase()}">${p.size||'?'}</span><span class="pname" title="${p.name}">${p.name}</span><span class="gh-link" data-gh="${p.name}/blob/${p.branch}" title="GitHub 仓库"><i data-lucide="github" class="icon"></i></span></div><div class="bar-track" title="高+中 ${hm} / 可测 ${s.testable}"><div class="bar-fill ${s.high>0?'warn':'info'}" style="width:${s.testable?Math.round(hm/s.testable*100):0}%"></div></div><div class="dash-levrow"><span class="level-badge level-high">🟢 ${s.high}</span><span class="level-badge level-mid">⚖ ${s.mid}</span><span class="level-badge level-low">💤 ${s.low}</span>${s.reviewPending?`<span class="level-badge level-exempt">⏳ ${s.reviewPending}</span>`:''}</div><div class="dash-gap ${gapCls}">${s.noCoverHigh>0?'⚠':'✓'} 高优无覆盖: <span class="n">${s.noCoverHigh}</span></div><div class="dash-cover"><span>覆盖 ${s.withCover}/${s.testable}</span><span>${cov}%</span></div><div class="dash-cover"><div class="bar-track" style="flex:1"><div class="bar-fill" style="width:${cov}%"></div></div></div>${p.generatedAt?`<div class="dash-meta"><span>${p.generatedAt.slice(0,10)||''}</span><span>${p.baseSha?'SHA '+p.baseSha:''}</span></div>`:''}${renderTestCardRow(p.name)}<div class="test-run-row"><div class="run-dd-wrap"><button class="test-run-btn" data-run="${p.name}"><i data-lucide="play" class="icon"></i>运行</button><div class="export-dropdown" id="run-dd-${p.name}"><div class="export-dropdown-item" data-run-mode="full" data-run-name="${p.name}">🔧 完整流程 (配置→编译→测试→覆盖)</div><div class="export-dropdown-item" data-run-mode="test-only" data-run-name="${p.name}">⚡ 仅测试 (跳过编译)</div><div class="export-dropdown-item" data-run-mode="test+coverage" data-run-name="${p.name}">📊 测试+覆盖</div><div class="export-dropdown-item" data-run-mode="build+test" data-run-name="${p.name}">🔨 编译+测试</div><div class="export-dropdown-item" data-run-mode="coverage-only" data-run-name="${p.name}">📈 仅覆盖率</div><div class="export-dropdown-item" data-run-mode="script" data-run-name="${p.name}">📜 项目脚本 (run-ut.sh)</div></div></div></div></div>`}).join('')}
function renderKanban(){const l=dashFiltered();const mk=(cards,cls,icon)=>cards.map(p=>{const n=cls==='todo'?p.stats.noCoverHigh:cls==='sched'?p.stats.noCoverMid:p.stats.withCover;const dots=Math.min(10,Math.ceil(n/100));const dotColor=cls==='todo'?'var(--danger,#ef4444)':cls==='sched'?'var(--warn)':'var(--accent)';return`<div class="kanban-card" data-proj="${p.name}"><div class="krow"><span>${icon} ${p.name}</span><b>${n}</b></div>${dots>1?`<div class="dot-blocks">${Array(dots).fill(`<span class="dot-block" style="background:${dotColor}"></span>`).join('')}</div>`:''}</div>`}).join('');const todo=l.filter(p=>p.stats.noCoverHigh>0),sched=l.filter(p=>p.stats.noCoverHigh===0&&p.stats.noCoverMid>0),done=l.filter(p=>p.stats.withCover>0);$('#dash-kanban').innerHTML=`<div class="kanban-col"><div class="kanban-col-head">🔴 高优·无覆盖 <span class="badge">${todo.length}</span></div><div class="kanban-col-body">${mk(todo,'todo')||'<div class="dash-empty" style="padding:20px">✓ 无</div>'}</div></div><div class="kanban-col"><div class="kanban-col-head">🟡 中优·无覆盖 <span class="badge">${sched.length}</span></div><div class="kanban-col-body">${mk(sched,'sched')||'<div class="dash-empty" style="padding:20px">✓ 无</div>'}</div></div><div class="kanban-col"><div class="kanban-col-head">🟢 已有测试覆盖 <span class="badge">${done.length}</span></div><div class="kanban-col-body">${mk(done,'done')||'<div class="dash-empty" style="padding:20px">—</div>'}</div></div>`}
function renderTrend(){let h=[];try{h=JSON.parse(localStorage.getItem('utie-dash-history')||'[]')}catch(e){}const el=$('#dash-trend');if(h.length<2){el.innerHTML='<div class="dash-empty"><i data-lucide="trending-up" class="icon"></i><div style="margin-top:8px">快照不足（需≥2次刷新）— 每次刷新自动记录</div></div>';return}const W=760,H=220,P=36;const ts=h.map(x=>x.ts),yv=h.map(x=>x.noCoverHigh+x.noCoverMid);const t0=ts[0],t1=ts[ts.length-1]||1;const ymax=Math.max(...yv,1)*1.1;const px=t=>P+(W-2*P)*((t-t0)/Math.max(1,t1-t0));const py=v=>H-P-(H-2*P)*(v/ymax);const pts=yv.map((v,i)=>px(ts[i]).toFixed(1)+','+py(v).toFixed(1)).join(' ');const area=`${P},${H-P} ${pts} ${px(ts[ts.length-1]).toFixed(1)},${H-P}`;el.innerHTML=`<div class="trend-title">高/中优无覆盖总数趋势（${h.length} 次快照）</div><svg viewBox="0 0 ${W} ${H}"><polygon points="${area}" fill="var(--accent-soft)"/><polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>${yv.map((v,i)=>`<circle cx="${px(ts[i]).toFixed(1)}" cy="${py(v).toFixed(1)}" r="3" fill="var(--accent)"><title>${new Date(ts[i]).toLocaleString()}\n高+中无覆盖: ${v}</title></circle>`).join('')}<line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}" stroke="var(--border-soft)"/><text x="${P}" y="${H-P+16}" fill="var(--text-muted)" font-size="11">${new Date(t0).toLocaleDateString()}</text><text x="${W-P}" y="${H-P+16}" text-anchor="end" fill="var(--text-muted)" font-size="11">${new Date(t1).toLocaleDateString()}</text><text x="${P-6}" y="${py(ymax)+4}" text-anchor="end" fill="var(--text-muted)" font-size="11">${Math.round(ymax)}</text><text x="${P-6}" y="${H-P}" text-anchor="end" fill="var(--text-muted)" font-size="11">0</text></svg>`}
function openDashDrawer(name){const p=S.dash.projects.find(x=>x.name===name);if(!p)return;$('#dash-drawer-title').textContent=name;const s=p.stats,tot=Math.max(1,s.high+s.mid+s.low);const cH=s.high/tot*100,cM=s.mid/tot*100;const pie=`conic-gradient(var(--accent) 0 ${cH}%,var(--warn) ${cH}% ${cH+cM}%,var(--border-soft) ${cH+cM}% 100%)`;const gaps=(p.topGap||[]).map(g=>`<div class="gap-item" data-gapfile="${g.file||''}" data-gapname="${g.name||''}"><div class="nm">${g.name||g.qn}</div><div class="fl">${g.class||''} · ${g.file||''}${g.score?' · 分数 '+g.score:''}</div></div>`).join('');$('#dash-drawer-body').innerHTML=`<div class="dash-levrow"><span class="level-badge level-high">🟢 ${s.high}</span><span class="level-badge level-mid">⚖ ${s.mid}</span><span class="level-badge level-low">💤 ${s.low}</span><span class="level-badge level-exempt">🚫 ${Math.max(0,s.testable-s.high-s.mid-s.low)}</span>${s.reviewPending?`<span class="level-badge level-exempt">⏳ 待复核 ${s.reviewPending}</span>`:''}</div><div class="pie-wrap"><div class="pie" style="background:${pie}"></div><div class="pie-legend"><div class="row"><span class="sw" style="background:var(--accent)"></span>high ${s.high}</div><div class="row"><span class="sw" style="background:var(--warn)"></span>mid ${s.mid}</div><div class="row"><span class="sw" style="background:var(--border-soft)"></span>low ${s.low}</div></div></div><div class="collapsible"><div class="collapsible-head" onclick="this.parentElement.classList.toggle('collapsed')"><span class="ctitle">⚠ 高优无覆盖 Top 10</span><button type="button" class="collapse-btn" tabindex="-1" aria-label="折叠"><i data-lucide="chevron-right" class="chev"></i></button></div><div class="collapsible-body">${gaps||'<div class="detail-small text-[var(--text-muted)]">无数据</div>'}</div></div><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm btn-primary" id="dash-open-editor"><i data-lucide="external-link" class="icon"></i> 在编辑器中打开</button><button class="btn btn-sm" id="dash-open-gh"><i data-lucide="github" class="icon"></i> GitHub</button></div>${p.generatedAt?`<div class="dash-meta"><span>数据: ${p.generatedAt}</span><span>${p.baseSha||''}</span><span title="分支来源: ${p.branchSource||''}">🌿 ${p.branch}</span></div>`:''}<div id="dash-test-run-panel"></div><div id="dash-test-section" class="test-section"><div class="test-loading"><div class="spin"></div>加载测试结果…</div></div>`;$('#dash-drawer').classList.add('open');$('#dash-drawer-overlay').classList.add('open');$('#dash-open-gh').onclick=()=>window.open('https://github.com/linuxdeepin/'+name+(p.branch?'/tree/'+p.branch:''),'_blank');$('#dash-open-editor').onclick=()=>dashOpenInEditor(name);try{lucide.createIcons()}catch(e){}syncDrawerMaxIcon();loadDrawerTestResults(name);renderDrawerRunPanel(name);}
function closeDashDrawer(){$('#dash-drawer').classList.remove('open','maximized');$('#dash-drawer-overlay').classList.remove('open');syncDrawerMaxIcon()}
function syncDrawerMaxIcon(){const btn=$('#dash-drawer-maximize');if(!btn)return;const isMax=$('#dash-drawer').classList.contains('maximized');btn.innerHTML='<i data-lucide="'+(isMax?'minimize-2':'maximize-2')+'" class="icon"></i>';btn.title=isMax?'还原':'最大化';try{lucide.createIcons()}catch(e){}}
function dashCoverageUrl(name){const b=dashBase().replace(/\/$/,'');const theme=document.documentElement.getAttribute('data-theme')||'dark';return `${b}/api/coverage/${name}/?theme=${theme}`}
async function fetchTestResults(name){if(S.dash.testResults[name])return S.dash.testResults[name];try{const r=await dashApi('/api/test/results/'+name);const d=await r.json();S.dash.testResults[name]=d;return d}catch(e){return{project:name,available:false,reason:'fetch error: '+e.message}}}
function renderTestCardRow(name){const st=S.dash.testRunStatus&&S.dash.testRunStatus[name];if(st&&(st.state==='running'||st.state==='queued')){const pct=st.progress?' '+st.progress:'';return`<div class="test-status-card"><div class="spin"></div>${st.label||st.phase||'运行中'}${pct} · ${st.elapsed||0}s</div>`}const t=S.dash.testResults[name];if(st&&st.state==='done'){const t2=S.dash.testResults[name];if(t2&&t2.available){const s=t2.test_summary||{};const dot=s.failed>0?'ng':'ok';return`<div class="test-card-row"><span class="tc-dot ${dot}"></span>✓ 测试 ${s.passed||0}/${s.total||0}${s.line_coverage?` · 覆盖 ${s.line_coverage}`:''}</div>`}}if(!t||!t.available)return'';const s=t.test_summary||{};const dot=s.failed>0?'ng':'ok';return`<div class="test-card-row"><span class="tc-dot ${dot}"></span>测试 ${s.passed||0}/${s.total||0}${s.line_coverage?` · 覆盖 ${s.line_coverage}`:''}</div>`}
function renderTestSection(t,name){if(!t||!t.available){const reason=t&&t.reason?t.reason:'no build dir';return`<div class="test-section-title">📊 测试结果</div><div class="detail-small text-[var(--text-muted)]">${reason==='no build dir'?'该项目暂无本地构建目录（build-ut），需先运行测试。':'无本地测试数据 ('+reason+')'}</div>`}const s=t.test_summary||{};const suites=t.test_suites||[];const fails=t.failed_cases||[];const hasCov=t.coverage_html_available;const suiteHtml=suites.map(su=>{const ok=su.tests-su.failures;const cases=su.cases||[];const caseHtml=cases.map(c=>{const isFail=c.failure||c.result==='failed';return`<div class="test-case"><span class="dot ${isFail?'ng':'ok'}"></span><span class="cn">${escapeHtml(c.name)}</span><span class="tm">${(+c.time).toFixed(3)}s</span></div>`}).join('');return`<div class="test-suite"><div class="test-suite-head" onclick="this.parentElement.classList.toggle('open')"><i data-lucide="chevron-right" class="chev"></i><span class="sn">${escapeHtml(su.name)}</span><span class="test-badge ${su.failures>0?'fail':'pass'}">${ok}/${su.tests}</span></div><div class="test-suite-body">${caseHtml}</div></div>`}).join('');const failHtml=fails.slice(0,20).map(f=>`<div class="test-fail"><div class="fn">${escapeHtml(f.suite)}::${escapeHtml(f.name)}</div><div class="msg">${escapeHtml(f.failure||'(无失败消息)')}</div></div>`).join('');const covIframe=hasCov?`<iframe class="coverage-iframe" id="cov-frame" src="${dashCoverageUrl(name)}" sandbox="allow-same-origin allow-popups" loading="lazy"></iframe>`:'';return`<div class="test-section-title">📊 测试结果${t.last_run?` <span class="detail-small text-[var(--text-muted)]">${t.last_run.slice(0,19)}</span>`:''}</div><div class="test-summary"><span class="test-badge ${s.failed>0?'fail':'pass'}">✓ 通过 ${s.passed||0}</span>${s.failed>0?`<span class="test-badge fail">✗ 失败 ${s.failed}</span>`:''}<span class="test-badge muted">共 ${s.total||0}</span>${s.line_coverage?`<span class="test-badge cov">📊 行覆盖 ${s.line_coverage}</span>`:''}${s.function_coverage?`<span class="test-badge cov">🔧 函数 ${s.function_coverage}</span>`:''}</div>${failHtml?`<div class="detail-body font-semibold" style="margin-top:4px">✗ 失败用例 (${fails.length})</div>${failHtml}`:''}${hasCov?`<div class="collapsible"><div class="collapsible-head" onclick="this.parentElement.classList.toggle('collapsed')"><span class="ctitle" style="flex:none">📈 覆盖率报告</span><span class="cov-path" id="cov-path" title=""></span><button class="btn btn-sm btn-ghost" id="cov-back" title="后退 (iframe 历史)" disabled><i data-lucide="arrow-left" class="icon"></i></button><button class="btn btn-sm btn-ghost" id="cov-forward" title="前进 (iframe 历史)" disabled><i data-lucide="arrow-right" class="icon"></i></button><button class="btn btn-sm btn-ghost" id="cov-refresh" title="刷新覆盖率报告"><i data-lucide="refresh-cw" class="icon"></i></button><button type="button" class="collapse-btn" tabindex="-1" aria-label="折叠"><i data-lucide="chevron-right" class="chev"></i></button></div><div class="collapsible-body">${covIframe}</div></div>`:''}${suites.length?`<div class="collapsible"><div class="collapsible-head" onclick="this.parentElement.classList.toggle('collapsed')"><span class="ctitle">测试套件 (${suites.length})</span><button type="button" class="collapse-btn" tabindex="-1" aria-label="折叠"><i data-lucide="chevron-right" class="chev"></i></button></div><div class="collapsible-body">${suiteHtml}</div></div>`:''}`}
async function loadDrawerTestResults(name){const el=$('#dash-test-section');if(!el)return;el.innerHTML='<div class="test-loading"><div class="spin"></div>加载测试结果…</div>';const t=await fetchTestResults(name);el.innerHTML=renderTestSection(t,name);try{lucide.createIcons()}catch(e){}const f=$('#cov-frame');const cs={base:null,pos:0,last:0,dir:null};const backBtn=$('#cov-back'),fwdBtn=$('#cov-forward'),pathEl=$('#cov-path');function updCovNav(){if(!f||!f.contentWindow)return;try{const w=f.contentWindow;const len=w.history.length||0;const loc=w.location;const here=loc.pathname+(loc.hash||'');if(cs.base===null){cs.base=len;cs.pos=0}else if(cs.dir==='back'){cs.pos=Math.max(0,cs.pos-1);cs.dir=null}else if(cs.dir==='forward'){cs.pos=Math.min(Math.max(0,len-cs.base),cs.pos+1);cs.dir=null}else if(len>cs.last){cs.pos=len-cs.base}cs.last=len;const max=Math.max(0,len-cs.base);if(backBtn)backBtn.disabled=cs.pos<=0;if(fwdBtn)fwdBtn.disabled=cs.pos>=max;if(pathEl){const fn=(here.split('/').pop()||'').split('?')[0]||'index';pathEl.textContent=fn;pathEl.title=here}}catch(e){}}if(f)f.addEventListener('load',updCovNav);if(backBtn)backBtn.onclick=(e)=>{e.stopPropagation();if(f&&f.contentWindow){cs.dir='back';try{f.contentWindow.history.back()}catch(_){}}};if(fwdBtn)fwdBtn.onclick=(e)=>{e.stopPropagation();if(f&&f.contentWindow){cs.dir='forward';try{f.contentWindow.history.forward()}catch(_){}}};const refBtn=$('#cov-refresh');if(refBtn)refBtn.onclick=(e)=>{e.stopPropagation();if(!f)return;cs.base=null;cs.pos=0;cs.last=0;cs.dir=null;f.src=f.src.split('?')[0]+'?t='+Date.now()+'&theme='+(document.documentElement.getAttribute('data-theme')||'dark')}}
async function probeDashServer(){const el=$('#dash-conn');$('#dash-conn-text').textContent='检测服务…';el.className='dash-conn';el.style.cursor='pointer';el.title='点击重新检测';const saved=dashBase();const cands=[...new Set([saved,'','http://localhost:8765','http://127.0.0.1:8765','http://localhost:8766','http://localhost:8767','http://localhost:9000'])].filter(x=>x!==null);let d=null,hit=null;for(const b of cands){try{const ctl=new AbortController();const tm=setTimeout(()=>ctl.abort(),1500);const r=await fetch((b?b.replace(/\/$/,''):'')+'/api/status',{signal:ctl.signal});clearTimeout(tm);if(!r.ok)continue;const j=await r.json();if(!j.server)continue;d=j;hit=b;break}catch(e){}}if(d){try{localStorage.setItem(DASH_SERVER_KEY,hit||window.location.origin||'http://localhost:8765')}catch(e){}S.dash.server=d;el.className='dash-conn ok';$('#dash-conn-text').textContent='● server已连接'+(d.mcp?' · MCP✓':' · MCP✗');$('#btn-dash-sync').disabled=!d.mcp;await loadDashProjects();toast('已连接看板 server · '+(d.projects_cached||0)+' 项目')}else{S.dash.server=null;el.className='dash-conn bad';$('#dash-conn-text').textContent='○ 离线 · 点击重试';$('#btn-dash-sync').disabled=true;try{const c=localStorage.getItem('utie-dash-projects');if(c){S.dash.projects=JSON.parse(c);renderDash();toast('离线模式 · 显示上次缓存 '+(S.dash.projects.length||0)+' 项目','warn')}}catch(ex){}}}
async function loadDashProjects(){try{const r=await dashApi('/api/projects');const d=await r.json();S.dash.projects=(d.projects||[]).map(normDashProject);saveDashCache();saveDashSnapshot();renderDash()}catch(e){toast('拉取项目列表失败: '+e.message,'err')}}
async function startDashSync(){if(!S.dash.server){toast('无 server 连接，请先启动 dashboard-server.py','warn');return}if(S.dash.syncing){toast('同步进行中…','warn');return}S.dash.syncing=true;$('#btn-dash-sync').disabled=true;$('#sync-float').classList.remove('hidden');$('#sync-log').classList.add('hidden');try{const r=await dashApi('/api/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});const d=await r.json();S.dash.task=d.task_id;pollDashTask()}catch(e){S.dash.syncing=false;$('#btn-dash-sync').disabled=false;toast('同步启动失败: '+e.message,'err')}}
async function pollDashTask(){if(!S.dash.task)return;try{const r=await dashApi('/api/task/'+S.dash.task);const t=await r.json();const total=t.total_n||26,done=t.done_n||0;$('#sync-title').textContent=t.state==='done'?'✓ 同步完成':('同步中: '+(t.current||'…'));$('#sync-n').textContent=done+'/'+total;$('#sync-bar').style.width=(total?Math.round(done/total*100):0)+'%';if(t.log_tail)$('#sync-log').textContent=t.log_tail;document.querySelectorAll('.dash-card').forEach(c=>{const p=c.dataset.proj;if(t.state==='running'&&p===t.current)c.classList.add('syncing');else c.classList.remove('syncing')});if(t.state==='done'){S.dash.syncing=false;S.dash.task=null;$('#btn-dash-sync').disabled=false;toast('✅ 同步完成 ('+t.elapsed+'s)');await loadDashProjects();setTimeout(()=>$('#sync-float').classList.add('hidden'),2500);return}if(t.state==='error'){S.dash.syncing=false;$('#btn-dash-sync').disabled=false;toast('同步失败','err');return}}catch(e){}if(S.dash.task)setTimeout(pollDashTask,2000)}
async function importDashFiles(files){const got=[];for(const f of files){try{const d=JSON.parse(await f.text());if(Array.isArray(d.results)){for(const r of d.results){if(r.stats)got.push(normDashProject({name:r.project,mcp_name:r.mcp_name,stats:{total_methods:r.stats.total_methods,testable:r.stats.testable,high:r.stats.high,mid:r.stats.mid,with_test_cover:r.stats.with_test_cover,no_cover_high:r.stats.no_cover_high,no_cover_mid:r.stats.no_cover_mid}}))}}else if(d.methods){const ms=d.methods,tb=ms.filter(m=>m.testable!==false);const cnt=f2=>tb.filter(f2).length;got.push(normDashProject({name:(d.project||f.name).replace(/^home-uos-service-codebase-repos-/,'').replace(/\.ut-inventory\.json$/,''),generated_at:d.generated_at||'',base_sha:(d.base_sha||'').slice(0,10),stats:{total_methods:ms.length,testable:tb.length,high:cnt(m=>m.level==='high'),mid:cnt(m=>m.level==='mid'),low:cnt(m=>m.level==='low'),review_pending:(d.review_queue||[]).filter(r=>r.status==='pending').length,with_test_cover:cnt(m=>(m.test_cover_count||0)>0),no_cover_high:cnt(m=>m.level==='high'&&(m.test_cover_count||0)===0),no_cover_mid:cnt(m=>m.level==='mid'&&(m.test_cover_count||0)===0)},top_gap:tb.filter(m=>m.level==='high'&&(m.test_cover_count||0)===0).sort((a,b)=>(b.score||0)-(a.score||0)).slice(0,10).map(m=>({name:m.name,class:m.class_qn,file:m.file_path,score:m.score||0}))}))}}catch(e){toast(f.name+' 解析失败: '+e.message,'err')}}if(got.length){const map=new Map(S.dash.projects.map(p=>[p.name,p]));for(const p of got)map.set(p.name,p);S.dash.projects=[...map.values()];saveDashCache();saveDashSnapshot();renderDash();toast('✅ 导入 '+got.length+' 项目')}}
function initDashEvents(){$$('.view-tab').forEach(t=>t.onclick=()=>switchView(t.dataset.view));$('#dash-conn').onclick=()=>probeDashServer();$$('.dash-subtab').forEach(t=>t.onclick=()=>{S.dash.sub=t.dataset.dsub;renderDash()});$('#btn-dash-sync').onclick=startDashSync;$('#btn-dash-import').onclick=()=>$('#dash-import-input').click();$('#dash-import-input').onchange=e=>{importDashFiles([...e.target.files]);e.target.value=''};$('#dash-search').oninput=()=>{if(S.view==='dashboard')renderDash()};initDashDropdown('#dash-size-wrap','#dash-size-btn','#dash-size-dd','#dash-size-label',()=>{if(S.view==='dashboard')renderDash()});initDashDropdown('#dash-sort-wrap','#dash-sort-btn','#dash-sort-dd','#dash-sort-label',()=>{if(S.view==='dashboard')renderDash()});$('#dash-onlygap').onchange=()=>{if(S.view==='dashboard')renderDash()};$('#dash-drawer-close').onclick=closeDashDrawer;$('#dash-drawer-overlay').onclick=closeDashDrawer;$('#dash-drawer-maximize').onclick=()=>{const d=$('#dash-drawer');const isMax=d.classList.toggle('maximized');const btn=$('#dash-drawer-maximize');btn.innerHTML='<i data-lucide="'+(isMax?'minimize-2':'maximize-2')+'" class="icon"></i>';btn.title=isMax?'还原':'最大化';try{lucide.createIcons()}catch(e){}};$('#sync-collapse').onclick=()=>$('#sync-log').classList.toggle('hidden');document.addEventListener('click',e=>{const gap=e.target.closest('.gap-item');if(gap){const f=gap.dataset.gapfile||'',n=gap.dataset.gapname||'',pr=$('#dash-drawer-title').textContent.trim();if(!f||!n)return;const br=dashBranchFor(pr)||currentBranch();const url='https://github.com/linuxdeepin/'+pr+'/blob/'+br+'/'+f;closeDashDrawer();openGitHubPanel(pr,br,f,n,url,pr+' · '+n,'right');return}const gh=e.target.closest('[data-gh]');if(gh){e.stopPropagation();window.open('https://github.com/linuxdeepin/'+gh.dataset.gh,'_blank');return}const card=e.target.closest('.dash-card[data-proj]');if(card&&!e.target.closest('.run-dd-wrap')&&!e.target.closest('.test-status-card')){openDashDrawer(card.dataset.proj);return}const kc=e.target.closest('.kanban-card[data-proj]');if(kc){openDashDrawer(kc.dataset.proj)}});initTestRunEvents()}

// ═══ Phase 2: 测试运行触发 + 状态轮询 ═══
S.dash.testRunStatus={};          // name → {state, phase, progress, log_tail, elapsed, ...}
S.dash.testPolling=false;         // 轮询定时器标志
S.dash.testMode='test-only';      // 当前选中模式（drawer 面板）

const TEST_MODE_LABELS={
  'full':'🔧 完整流程','test-only':'⚡ 仅测试','test+coverage':'📊 测试+覆盖',
  'build+test':'🔨 编译+测试','coverage-only':'📈 仅覆盖率','script':'📜 项目脚本'
};

function initTestRunEvents(){
  // 卡片上的"运行"按钮 → 打开下拉
  document.addEventListener('click',e=>{
    const runBtn=e.target.closest('.test-run-btn[data-run]');
    if(runBtn){
      e.stopPropagation();
      const name=runBtn.dataset.run;
      const dd=document.getElementById('run-dd-'+name);
      if(dd){
        $$('.export-dropdown.show').forEach(d=>{if(d!==dd)d.classList.remove('show')});
        dd.classList.toggle('show');
      }
      return;
    }
    // 下拉项 → 触发运行
    const item=e.target.closest('.export-dropdown-item[data-run-mode]');
    if(item){
      e.stopPropagation();
      const name=item.dataset.runName,mode=item.dataset.runMode;
      const dd=item.closest('.export-dropdown');
      if(dd)dd.classList.remove('show');
      dashRunTest(name,mode);
      return;
    }
    // 点外部关闭所有 run 下拉
    if(!e.target.closest('.run-dd-wrap')){
      $$('.export-dropdown.show').forEach(d=>{
        if(d.id&&d.id.startsWith('run-dd-'))d.classList.remove('show');
      });
    }
  });
}

async function dashRunTest(name,mode){
  if(!S.dash.server){toast('无 server 连接','warn');return}
  toast(`▶ 启动 ${name} · ${TEST_MODE_LABELS[mode]||mode}`);
  S.dash.testRunStatus[name]={state:'queued',phase:'',progress:'',label:'排队中',elapsed:0,mode};
  renderDashGridIfVisible();
  renderDrawerRunPanelIfOpen(name);
  try{
    const r=await dashApi('/api/test/run/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});
    const d=await r.json();
    if(!d.started&&!d.queued){
      toast('启动失败: '+(d.error||'未知'),'err');
      delete S.dash.testRunStatus[name];
      renderDashGridIfVisible();
    }
    startTestPolling();
  }catch(e){
    toast('启动失败: '+e.message,'err');
    delete S.dash.testRunStatus[name];
    renderDashGridIfVisible();
  }
}

async function dashStopTest(name){
  try{
    await dashApi('/api/test/stop/'+name,{method:'POST',body:'{}'});
    toast('⏹ 已发送终止信号');
  }catch(e){toast('终止失败: '+e.message,'err')}
}

function startTestPolling(){
  if(S.dash.testPolling)return;
  S.dash.testPolling=true;
  pollTestStatus();
}

async function pollTestStatus(){
  if(!S.dash.server){S.dash.testPolling=false;return}
  let anyActive=false;
  try{
    const r=await dashApi('/api/test/status');
    const d=await r.json();
    const running=d.running||[];
    for(const st of running){
      S.dash.testRunStatus[st.project]=st;
      if(st.state==='running'||st.state==='queued')anyActive=true;
    }
    // 清理已不在列表中但之前 active 的（server cleanup 后）
    const activeNames=new Set(running.map(s=>s.project));
    for(const name of Object.keys(S.dash.testRunStatus)){
      if(!activeNames.has(name)){
        const prev=S.dash.testRunStatus[name];
        if(prev&&(prev.state==='running'||prev.state==='queued')){
          // server 已清理，标记为 done（结果应已更新）
          S.dash.testRunStatus[name]={...prev,state:'done'};
          // 刷新测试结果缓存
          delete S.dash.testResults[name];
          fetchTestResults(name).then(()=>renderDashGridIfVisible());
        }
      }
    }
  }catch(e){}
  // 更新 UI
  renderDashGridIfVisible();
  const drawerName=$('#dash-drawer-title').textContent.trim();
  if(drawerName)renderDrawerRunPanelIfOpen(drawerName);
  // 有活跃任务 → 继续轮询
  if(anyActive){
    setTimeout(pollTestStatus,1500);
  }else{
    // 最后再拉一次确认结果
    setTimeout(async()=>{
      try{
        const r=await dashApi('/api/test/status');
        const d=await r.json();
        for(const st of(d.running||[])){
          S.dash.testRunStatus[st.project]=st;
        }
      }catch(e){}
      renderDashGridIfVisible();
      const dn=$('#dash-drawer-title').textContent.trim();
      if(dn)renderDrawerRunPanelIfOpen(dn);
      S.dash.testPolling=false;
    },2000);
  }
}

function renderDashGridIfVisible(){
  if(S.view==='dashboard'&&S.dash.sub==='overview')renderDashGrid();
}

function renderDrawerRunPanel(name){
  const el=$('#dash-test-run-panel');
  if(!el)return;
  const st=S.dash.testRunStatus[name];
  const isActive=st&&(st.state==='running'||st.state==='queued');
  const modes=Object.entries(TEST_MODE_LABELS);
  let html='<div class="test-run-panel">';
  html+='<div class="test-mode-row"><span class="test-mode-label">模式:</span>';
  for(const[m,l]of modes){
    const cls=(S.dash.testMode===m?'active':'')+(isActive?' disabled':'');
    html+=`<button class="test-mode-btn ${cls}" data-drawer-mode="${m}" ${isActive?'disabled':''}>${l}</button>`;
  }
  html+='</div>';
  html+='<div class="test-run-actions">';
  if(isActive){
    html+=`<button class="btn btn-sm" id="dash-test-stop"><i data-lucide="square" class="icon"></i> 停止</button>`;
    html+=`<span class="test-phase-badge">${st.label||st.phase||'运行中'} ${st.progress||''}</span>`;
    html+=`<span class="detail-small text-[var(--text-muted)]">${st.elapsed||0}s</span>`;
  }else{
    html+=`<button class="btn btn-sm btn-primary" id="dash-test-run"><i data-lucide="play" class="icon"></i> 运行测试</button>`;
    if(st&&st.state==='done'){
      html+=`<span class="test-phase-badge done">✓ 完成</span>`;
    }else if(st&&st.state==='failed'){
      html+=`<span class="test-phase-badge fail">✗ 失败</span>`;
    }
  }
  html+='</div>';
  // 进度条（build 阶段有百分比时）
  if(isActive&&st.progress){
    const pct=parseInt(st.progress)||0;
    html+=`<div class="test-progress-bar"><div class="fill" style="width:${pct}%"></div></div>`;
  }
  // 日志
  if(st&&st.log_tail){
    html+=`<div class="test-log-panel">${escapeHtml(st.log_tail)}</div>`;
  }
  html+='</div>';
  // 记住用户是否在底部（跟随尾部）
  const oldLog=el.querySelector('.test-log-panel');
  const wasAtBottom=oldLog?Math.abs(oldLog.scrollTop+oldLog.clientHeight-oldLog.scrollHeight)<30:true;
  el.innerHTML=html;
  // 日志自动滚到最下方（跟随输出）
  const logEl=el.querySelector('.test-log-panel');
  if(logEl&&wasAtBottom)logEl.scrollTop=logEl.scrollHeight;
  // 绑定事件
  const runBtn=$('#dash-test-run');
  if(runBtn)runBtn.onclick=()=>dashRunTest(name,S.dash.testMode);
  const stopBtn=$('#dash-test-stop');
  if(stopBtn)stopBtn.onclick=()=>dashStopTest(name);
  el.querySelectorAll('[data-drawer-mode]').forEach(b=>{
    b.onclick=()=>{
      if(b.disabled)return;
      S.dash.testMode=b.dataset.drawerMode;
      renderDrawerRunPanel(name);
    };
  });
  try{lucide.createIcons()}catch(e){}
}

function renderDrawerRunPanelIfOpen(name){
  const title=$('#dash-drawer-title').textContent.trim();
  if(title===name)renderDrawerRunPanel(name);
}
