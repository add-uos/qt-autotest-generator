// ═══ settings.js — 配置管理（全局设置 + 项目注册表）═══

let CFG = null;           // {config, projects} 来自 /api/config

async function dashJson(path, opts) {
    const r = await dashApi(path, opts);
    if (!r.ok) { let msg = 'HTTP ' + r.status; try { const e = await r.json(); msg = e.error || e.msg || msg; } catch (_) {} throw new Error(msg); }
    return r.json();
}

function switchViewSettings(v) {
    // 由 core.js switchView 调用的辅助：设置视图显隐
    $('#settings-view').classList.toggle('hidden', v !== 'settings');
}

async function loadConfig() {
    if (!S.dash.server) { $('#cfg-offline').classList.remove('hidden'); $('#cfg-tbody').innerHTML = ''; return; }
    try {
        const r = await dashJson('/api/config');
        if (!r || !r.projects) throw new Error('响应缺少 projects（服务版本过旧？重启 dashboard-server.py）');
        CFG = r;
        $('#cfg-offline').classList.add('hidden');
        $('#cfg-mcp-url').value = r.config?.mcp_url || '';
        $('#cfg-org').value = r.config?.github?.org || '';
        $('#cfg-port').value = r.config?.server?.port || 8765;
        $('#cfg-concurrency').value = r.config?.sync?.concurrency || 1;
        // 测试配置 (Phase 2)
        const tc = r.config?.test || {};
        $('#cfg-test-concurrent').value = tc.max_concurrent || 2;
        $('#cfg-test-timeout').value = tc.default_timeout || 600;
        const def = r.projects?.defaults || {};
        $('#cfg-def-branch').value = def.branch || 'master';
        $('#cfg-def-testdir').value = def.test_dir || 'autotests';
        $('#cfg-size-mode').value = (localStorage.getItem('utie-size-mode') || 'nodes');
        renderCfgRows();
    } catch (e) {
        $('#cfg-offline').classList.remove('hidden');
        toast('配置加载失败: ' + e.message, 'err');
    }
}

function renderCfgRows() {
    const tb = $('#cfg-tbody');
    if (!CFG) { tb.innerHTML = ''; return; }
    tb.innerHTML = '';
    CFG.projects.projects.forEach(p => { const [tr, expTr] = cfgRow(p); tb.appendChild(tr); tb.appendChild(expTr); });
    $('#cfg-proj-count').textContent = `(${CFG.projects.projects.length} 项)`;
    applyCfgFilter();
}

// ═══ 项目表搜索过滤 ═══
function cfgRowText(tr) {
    // 行内所有数据：单元格文本 + 输入框/下拉值 + title 属性（mcp 名在 name 格 title 里）
    const parts = [tr.innerText || ''];
    tr.querySelectorAll('input,select').forEach(el => { if (el.type !== 'checkbox') parts.push(el.value); });
    tr.querySelectorAll('[title]').forEach(el => parts.push(el.title));
    return parts.join(' ').toLowerCase();
}

function applyCfgFilter() {
    const tb = $('#cfg-tbody');
    const q = ($('#cfg-search-input')?.value || '').trim().toLowerCase();
    const total = CFG ? CFG.projects.projects.length : 0;
    let shown = 0;
    [...tb.rows].forEach(tr => {
        if (tr.classList.contains('cfg-expand-row')) return; // 随主行处理
        const expTr = tr.nextElementSibling;
        const hit = !q || cfgRowText(tr).includes(q) || (expTr && cfgRowText(expTr).includes(q));
        tr.style.display = hit ? '' : 'none';
        if (expTr && expTr.classList.contains('cfg-expand-row')) expTr.style.display = hit ? '' : 'none';
        if (hit) shown++;
    });
    $('#cfg-proj-count').textContent = q ? `(匹配 ${shown} / 共 ${total} 项)` : `(${total} 项)`;
}

function toggleCfgSearch(show) {
    const inp = $('#cfg-search-input');
    const btn = $('#cfg-search-btn');
    const on = show ?? inp.classList.contains('hidden');
    inp.classList.toggle('hidden', !on);
    btn.classList.toggle('hidden', on);
    if (on) {
        inp.focus();
    } else {
        inp.value = '';
        applyCfgFilter();
    }
}

function escAttr(v) { return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }

function cfgRow(p) {
    const tr = document.createElement('tr');
    if (!p.enabled) tr.className = 'cfg-row-off';
    const b = p.build || {}, src = p.source || {}, git = p.git || {};
    tr.innerHTML = `
<td><input type="checkbox" data-f="enabled" ${p.enabled !== false ? 'checked' : ''}/></td>
<td class="cfg-proj-name" title="mcp: ${escAttr(p.mcp_name || '—')}">${escAttr(p.name)}</td>
<td><input type="text" data-f="size" value="${escAttr(p.size || '?')}" style="width:40px" title="nodes: ${p.nodes ?? '—'}${p.nodes != null ? ' (' + sizeFromNodesHint(p.nodes) + ')' : ''}"/></td>
<td><input type="text" data-f="branch" value="${escAttr(git.branch || 'master')}" style="width:70px"/></td>
<td><select data-f="source_type">
  ${['mcp', 'local'].map(t => `<option value="${t}" ${src.type === t ? 'selected' : ''}>${t}</option>`).join('')}
</select></td>
<td class="cfg-path"><div style="display:flex;gap:4px"><input type="text" data-f="path" value="${escAttr(src.path || '')}" placeholder="/home/you/code/project"/><button class="btn btn-sm btn-ghost fs-pick-btn" title="浏览选择目录">…</button></div></td>
<td><select data-f="build_system">
  ${['cmake', 'qmake', 'meson', 'make', 'custom'].map(s => `<option value="${s}" ${(b.system || 'cmake') === s ? 'selected' : ''}>${s}</option>`).join('')}
</select></td>
<td><input type="text" data-f="test_dir" value="${escAttr(b.test_dir || '')}" placeholder="autotests" style="width:64px"/></td>
<td><input type="text" data-f="build_dir" value="${escAttr(b.build_dir || '')}" placeholder="build-ut" style="width:80px"/></td>
<td class="cfg-script"><div style="display:flex;gap:4px"><input type="text" data-f="script" value="${escAttr(b.script || '')}" placeholder="autotests/run-ut.sh"/><button class="btn btn-sm btn-ghost fs-pick-script-btn" title="浏览选择测试脚本">…</button></div></td>
<td><input type="text" data-f="test_cmd" value="${escAttr(b.test_cmd || '')}" placeholder="空=默认/脚本"/></td>
<td style="white-space:nowrap">
  <button class="btn btn-sm btn-ghost cfg-detect-btn" title="按本地路径探测构建系统">🔍</button>
  <button class="btn btn-sm btn-ghost cfg-del-btn" title="移除此行（保存后生效）" style="color:var(--exempt)">✕</button>
  <button class="btn btn-sm btn-ghost cfg-expand-btn" title="展开编辑面板">⋯</button>
</td>`;
    // 展开编辑面板：多列带标签，输入框更宽敞
    const expTr = document.createElement('tr');
    expTr.className = 'cfg-expand-row hidden';
    expTr.innerHTML = `<td colspan="12" class="cfg-expand-inner">
      <div class="cfg-panel">
        <div class="cfg-panel-head">
          <span class="cfg-panel-title">📝 <b>${escAttr(p.name)}</b> — 编辑项目配置</span>
          <button class="btn btn-sm btn-ghost cfg-panel-close" title="收起">✕</button>
        </div>
        <div class="cfg-panel-grid">
          <div class="cfg-cfgfield"><label>项目名</label><span class="cfg-val">${escAttr(p.name)}</span></div>
          <div class="cfg-cfgfield"><label>启用</label><input type="checkbox" data-f="enabled" ${p.enabled !== false ? 'checked' : ''} class="cfg-check"/></div>
          <div class="cfg-cfgfield"><label>规模</label><input type="text" data-f="size" value="${escAttr(p.size || '?')}"/></div>
          <div class="cfg-cfgfield"><label>分支</label><input type="text" data-f="branch" value="${escAttr(git.branch || 'master')}"/></div>
          <div class="cfg-cfgfield"><label>来源</label><select data-f="source_type">${['mcp','local'].map(t=>`<option value="${t}" ${src.type===t?'selected':''}>${t}</option>`).join('')}</select></div>
          <div class="cfg-cfgfield"><label>本地路径</label><div class="cfg-path-ctrl"><input type="text" data-f="path" value="${escAttr(src.path || '')}" placeholder="/home/you/code/project"/><button class="btn btn-sm btn-ghost fs-pick-btn">…</button></div></div>
          <div class="cfg-cfgfield"><label>构建系统</label><select data-f="build_system">${['cmake','qmake','meson','make','custom'].map(s=>`<option value="${s}" ${(b.system||'cmake')===s?'selected':''}>${s}</option>`).join('')}</select></div>
          <div class="cfg-cfgfield"><label>测试目录</label><input type="text" data-f="test_dir" value="${escAttr(b.test_dir || '')}" placeholder="autotests"/></div>
          <div class="cfg-cfgfield"><label>构建目录</label><input type="text" data-f="build_dir" value="${escAttr(b.build_dir || '')}" placeholder="build-ut"/></div>
          <div class="cfg-cfgfield"><label>测试脚本</label><div class="cfg-path-ctrl"><input type="text" data-f="script" value="${escAttr(b.script || '')}" placeholder="autotests/run-ut.sh"/><button class="btn btn-sm btn-ghost fs-pick-script-btn">…</button></div></div>
          <div class="cfg-cfgfield cfg-cfgfield-wide"><label>测试命令</label><input type="text" data-f="test_cmd" value="${escAttr(b.test_cmd || '')}" placeholder="空=默认/脚本"/></div>
        </div>
        <div class="cfg-panel-actions">
          <button class="btn btn-sm btn-ghost cfg-detect-btn">🔍 探测构建</button>
          <button class="btn btn-sm btn-ghost cfg-del-btn" style="color:var(--exempt)">✕ 删除</button>
        </div>
      </div>
    </td>`;
    // 通用双向绑定（紧凑行 + 面板共用同一套逻辑，绑定到同一个 p 对象）
    function bindDataF(row) {
        row.querySelectorAll('[data-f]').forEach(el => {
            const f = el.dataset.f;
            const handler = () => {
                let v = el.type === 'checkbox' ? el.checked : el.value;
                if (f === 'enabled') { p.enabled = v; tr.classList.toggle('cfg-row-off', !v); }
                else if (f === 'size') p.size = v.toUpperCase().slice(0, 2);
                else if (f === 'branch') { p.git = p.git || {}; p.git.branch = v; }
                else if (f === 'source_type') { p.source = p.source || {}; p.source.type = v; }
                else if (f === 'path') { p.source = p.source || {}; p.source.path = v.trim(); }
                else if (f === 'build_system') { p.build = p.build || {}; p.build.system = v; }
                else if (f === 'test_dir') { p.build = p.build || {}; p.build.test_dir = v.trim(); }
                else if (f === 'build_dir') { p.build = p.build || {}; p.build.build_dir = v.trim(); }
                else if (f === 'script') { p.build = p.build || {}; p.build.script = v.trim(); }
                else if (f === 'test_cmd') { p.build = p.build || {}; p.build.test_cmd = v.trim(); }
            };
            el.onchange = handler;
            if (el.type === 'text') el.oninput = handler;
        });
    }
    bindDataF(tr);
    bindDataF(expTr);
    // 展开/收起：同一时刻只展开一个面板
    function expandPanel(show) {
        expTr.classList.toggle('hidden', !show);
    }
    tr.querySelector('.cfg-expand-btn').onclick = e => {
        e.stopPropagation();
        const wasOpen = !expTr.classList.contains('hidden');
        // 关闭所有其它面板
        tr.closest('table').querySelectorAll('.cfg-expand-row').forEach(r => r.classList.add('hidden'));
        if (!wasOpen) expandPanel(true);
    };
    expTr.querySelector('.cfg-panel-close').onclick = e => { e.stopPropagation(); expandPanel(false); };
    // 删除行（紧凑行 + 面板按钮共用）
    function doDel() {
        const i = CFG.projects.projects.indexOf(p);
        if (i >= 0) CFG.projects.projects.splice(i, 1);
        tr.remove(); expTr.remove();
        $('#cfg-proj-count').textContent = `(${CFG.projects.projects.length} 项)`;
        toast(`已移除 ${p.name}（点「保存项目表」生效）`);
    }
    tr.querySelector('.cfg-del-btn').onclick = doDel;
    expTr.querySelector('.cfg-del-btn').onclick = doDel;
    // 浏览选择目录（紧凑行 + 面板）
    function syncPathInputs(v) {
        p.source = p.source || {}; p.source.type = 'local'; p.source.path = v;
        tr.querySelectorAll('[data-f=path]').forEach(el => el.value = v);
    }
    tr.querySelector('.fs-pick-btn').onclick = () => openFsPicker(p.source?.path || '', syncPathInputs);
    expTr.querySelector('.fs-pick-btn').onclick = () => openFsPicker(p.source?.path || '', syncPathInputs);
    // 浏览选择测试脚本（file 模式，折算成相对项目根路径）
    function syncScriptInputs(v) {
        const base = (p.source?.path || '').replace(/\/$/, '');
        let rel = v.startsWith(base + '/') ? v.slice(base.length + 1) : v;
        p.build = p.build || {}; p.build.script = rel;
        tr.querySelectorAll('[data-f=script]').forEach(el => el.value = rel);
    }
    tr.querySelector('.fs-pick-script-btn').onclick = () => openFsPicker(p.source?.path || '', syncScriptInputs, {file: true});
    expTr.querySelector('.fs-pick-script-btn').onclick = () => openFsPicker(p.source?.path || '', syncScriptInputs, {file: true});
    // 探测：一键填满 build_system/test_dir/build_dir/script（同步到两行的输入框）
    function doDetect() {
        const path = (p.source?.path || '').trim();
        if (!path) { toast('先填本地路径再探测', 'warn'); return; }
        dashJson('/api/config/detect', { method: 'POST', body: JSON.stringify({ path }) }).then(r => {
            if (!r.ok) { toast('探测失败: ' + r.msg, 'err'); return; }
            p.build = p.build || {};
            p.build.system = r.system;
            if (r.test_dir) p.build.test_dir = r.test_dir;
            if (r.build_dir) p.build.build_dir = r.build_dir;
            if (r.script) p.build.script = r.script;
            tr.querySelectorAll('[data-f=build_system]').forEach(el => el.value = r.system);
            if (r.test_dir) tr.querySelectorAll('[data-f=test_dir]').forEach(el => el.value = r.test_dir);
            if (r.build_dir) tr.querySelectorAll('[data-f=build_dir]').forEach(el => el.value = r.build_dir);
            if (r.script) tr.querySelectorAll('[data-f=script]').forEach(el => el.value = r.script);
            toast(`🔍 ${r.name_guess}: ${r.system} · ${r.build_dir || '-'} · ${r.script || '-'} (${(r.found || []).join(', ')})`);
        }).catch(e => toast('探测失败: ' + e.message, 'err'));
    }
    tr.querySelector('.cfg-detect-btn').onclick = doDetect;
    expTr.querySelector('.cfg-detect-btn').onclick = doDetect;
    return [tr, expTr];
}

async function saveGlobalCfg() {
    if (!CFG) return;
    CFG.config.mcp_url = $('#cfg-mcp-url').value.trim();
    CFG.config.github = CFG.config.github || {}; CFG.config.github.org = $('#cfg-org').value.trim();
    CFG.config.server = CFG.config.server || {}; CFG.config.server.port = parseInt($('#cfg-port').value, 10) || 8765;
    CFG.config.sync = CFG.config.sync || {}; CFG.config.sync.concurrency = Math.max(1, parseInt($('#cfg-concurrency').value, 10) || 1);
    // 测试配置 (Phase 2)
    CFG.config.test = CFG.config.test || {};
    CFG.config.test.max_concurrent = Math.max(1, parseInt($('#cfg-test-concurrent').value, 10) || 2);
    CFG.config.test.default_timeout = Math.max(30, parseInt($('#cfg-test-timeout').value, 10) || 600);
    try {
        const r = await dashJson('/api/config/global', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(CFG.config) });
        toast(r.ok ? '✓ ' + r.msg : '保存失败: ' + r.msg, r.ok ? '' : 'err');
    } catch (e) { toast('保存失败: ' + e.message, 'err'); }
}

function sizeFromNodesHint(n) { return n < 1000 ? 'S' : n < 5000 ? 'M' : n < 15000 ? 'L' : 'XL'; }

async function syncFromMcp() {
    if (!S.dash.server) { toast('需要伴随服务运行中', 'warn'); return; }
    const btn = $('#cfg-sync-mcp');
    btn.disabled = true; btn.textContent = '⏳ 同步中…';
    try {
        const keep = $('#cfg-size-mode').value === 'keep';
        const r = await dashJson('/api/config/sync-registry', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keep_size: keep })
        });
        if (!r.ok) { toast('同步失败: ' + (r.msg || '?'), 'err'); return; }
        localStorage.setItem('utie-size-mode', $('#cfg-size-mode').value);
        const parts = [`${r.mcp_total} 个 MCP 项目`];
        if (r.added?.length) parts.push(`新增 ${r.added.length}`);
        if (r.size_changed?.length) parts.push(`规模修正 ${r.size_changed.length}`);
        if (r.branch_changed?.length) parts.push(`分支修正 ${r.branch_changed.length}`);
        if (r.stale?.length) parts.push(`⚠ ${r.stale.length} 个已不在 MCP`);
        toast('🔄 ' + parts.join(' · ') + ' — 新增项目默认未启用');
        await loadConfig();
    } catch (e) { toast('同步失败: ' + e.message, 'err'); }
    finally { btn.disabled = false; btn.textContent = '🔄 从 MCP 同步'; }
}

async function saveProjectsCfg() {
    if (!CFG) return;
    CFG.projects.defaults = CFG.projects.defaults || {};
    CFG.projects.defaults.branch = $('#cfg-def-branch').value.trim() || 'master';
    CFG.projects.defaults.test_dir = $('#cfg-def-testdir').value.trim() || 'autotests';
    try {
        const r = await dashJson('/api/config/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(CFG.projects) });
        toast(r.ok ? `✓ ${r.msg}` : '保存失败: ' + r.msg, r.ok ? '' : 'err');
        if (r.ok) { loadConfig(); loadDashProjects(); }
    } catch (e) { toast('保存失败: ' + e.message, 'err'); }
}

function addCfgProjectRow() {
    if (!CFG) return;
    const name = prompt('项目名（GitHub 仓库名，如 deepin-calculator）:');
    if (!name || !/^[\w.\-]+$/.test(name)) { if (name) toast('项目名仅允许字母数字._-', 'warn'); return; }
    if (CFG.projects.projects.some(p => p.name === name)) { toast('项目已存在', 'warn'); return; }
    CFG.projects.projects.push({
        name, mcp_name: 'home-uos-service-codebase-repos-' + name, enabled: true, size: '?',
        git: { org: (CFG.config.github?.org || 'linuxdeepin'), branch: 'master' },
        source: { type: 'mcp', path: '' },
        build: { system: 'cmake', framework: 'gtest', configure: '', build_cmd: '', test_cmd: '', env: { QT_QPA_PLATFORM: 'offscreen' }, timeout: 600 },
    });
    renderCfgRows();
    toast(`已添加 ${name}，记得「保存项目表」`);
}

function initSettingsEvents() {
    $('#cfg-save-global').onclick = saveGlobalCfg;
    $('#cfg-save-projects').onclick = saveProjectsCfg;
    $('#cfg-add-project').onclick = addCfgProjectRow;
    $('#cfg-sync-mcp').onclick = syncFromMcp;
    $('#cfg-size-mode').onchange = e => localStorage.setItem('utie-size-mode', e.target.value);
    // 项目表搜索：按钮展开/收起，输入实时过滤
    $('#cfg-search-btn').onclick = () => toggleCfgSearch();
    $('#cfg-search-input').oninput = applyCfgFilter;
    $('#cfg-search-input').onkeydown = e => { if (e.key === 'Escape') toggleCfgSearch(false); };
    $('#cfg-search-input').onblur = () => { if (!$('#cfg-search-input').value.trim()) toggleCfgSearch(false); };
    // 目录浏览对话框
    $('#fs-cancel').onclick = fsClose;
    $('#fs-confirm').onclick = () => { if (fsPick.mode === 'file' && !fsPick.selected) { toast('请先选择一个文件', 'warn'); return; } const picked = fsPick.selected ? fsPick.path + '/' + fsPick.selected : fsPick.path; if (fsPick.cb && picked) fsPick.cb(picked); fsClose(); };
    $('#fs-home').onclick = () => fsLoad('~');
    $('#fs-up').onclick = () => fsLoad((fsPick.path || '/') + '/..');
    $('#fs-show-hidden').onchange = e => { fsPick.showHidden = e.target.checked; localStorage.setItem('utie-fs-hidden', fsPick.showHidden ? '1' : '0'); renderFsList(); };
    $('#fs-path-input').onkeydown = e => { if (e.key === 'Enter') fsLoad(e.target.value); };
    $('#fs-picker-modal').addEventListener('click', e => { if (e.target.id === 'fs-picker-modal') fsClose(); });
}

// ═══ 目录浏览对话框 ═══
const fsPick = { path: '', selected: '', cb: null, entries: [], mode: 'dir', showHidden: localStorage.getItem('utie-fs-hidden') === '1' };

async function openFsPicker(startPath, onPick, opts = {}) {
    if (!S.dash.server) { toast('目录浏览需要伴随服务运行中', 'warn'); return; }
    fsPick.cb = onPick;
    fsPick.mode = opts.file ? 'file' : 'dir';
    $('#fs-picker-modal').querySelector('h3').textContent = opts.file ? '📄 选择测试脚本' : '📁 选择项目本地路径';
    $('#fs-confirm').textContent = opts.file ? '✅ 选定此文件' : '✅ 选定此目录';
    $('#fs-show-hidden').checked = fsPick.showHidden;
    $('#fs-picker-modal').classList.remove('hidden');
    fsLoad(startPath || '~');
}

function fsClose() { $('#fs-picker-modal').classList.add('hidden'); fsPick.cb = null; }

async function fsLoad(raw) {
    try {
        const d = await dashJson('/api/fs/list?path=' + encodeURIComponent(raw));
        fsPick.path = d.path;
        $('#fs-path-input').value = d.path;
        fsPick.entries = d.entries || [];
        renderFsList();
    } catch (e) { toast('无法打开: ' + e.message, 'err'); }
}

function renderFsList() {
    const list = $('#fs-list');
    list.innerHTML = '';
    fsPick.selected = '';
    const shown = fsPick.entries.filter(e => fsPick.showHidden || !e.name.startsWith('.'));
    if (!shown.length) { list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">空目录</div>'; return; }
    shown.forEach(e => {
        const row = document.createElement('div');
        row.className = 'fs-row' + (e.dir ? '' : ' fs-file');
        row.textContent = (e.dir ? '📁 ' : '📄 ') + e.name + (e.symlink ? ' ↗' : '');
        row.dataset.name = e.name;
        // 单击选中，不导航
        row.onclick = () => {
            list.querySelectorAll('.fs-row.selected').forEach(r => r.classList.remove('selected'));
            row.classList.add('selected');
            fsPick.selected = e.name;
        };
        // 双击：目录→进入；文件(file 模式)→直接确认选择
        if (e.dir) row.ondblclick = () => fsLoad(fsPick.path + '/' + e.name);
        else if (fsPick.mode === 'file') row.ondblclick = () => { if (fsPick.cb) fsPick.cb(fsPick.path + '/' + e.name); fsClose(); };
        list.appendChild(row);
    });
}
