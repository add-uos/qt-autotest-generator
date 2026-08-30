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
    CFG.projects.projects.forEach(p => tb.appendChild(cfgRow(p)));
    $('#cfg-proj-count').textContent = `(${CFG.projects.projects.length} 项)`;
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
<td><input type="text" data-f="test_cmd" value="${escAttr(b.test_cmd || '')}" placeholder="空=ctest 默认"/></td>
<td style="white-space:nowrap">
  <button class="btn btn-sm btn-ghost cfg-detect-btn" title="按本地路径探测构建系统">🔍</button>
  <button class="btn btn-sm btn-ghost cfg-del-btn" title="移除此行（保存后生效）" style="color:var(--exempt)">✕</button>
</td>`;
    // 双向绑定到 CFG.projects.projects（删除时按对象身份定位，避免 idx 失效）
    tr.querySelectorAll('[data-f]').forEach(el => {
        const f = el.dataset.f;
        const handler = () => {
            let v = el.type === 'checkbox' ? el.checked : el.value;
            if (f === 'enabled') { p.enabled = v; tr.classList.toggle('cfg-row-off', !v); }
            else if (f === 'size') p.size = v.toUpperCase().slice(0, 2);
            else if (f === 'branch') { p.git = p.git || {}; p.git.branch = v; }
            else if (f === 'source_type') { p.source = p.source || {}; p.source.type = v; }
            else if (f === 'path') { p.source = p.source || {}; p.source.path = v.trim(); }
            else if (f === 'build_system') { p.build = p.build || {}; p.build.system = v; }
            else if (f === 'test_cmd') { p.build = p.build || {}; p.build.test_cmd = v.trim(); }
        };
        el.onchange = handler;
        if (el.type === 'text') el.oninput = handler;
    });
    // 删除行（按对象身份定位）
    tr.querySelector('.cfg-del-btn').onclick = () => {
        const i = CFG.projects.projects.indexOf(p);
        if (i >= 0) CFG.projects.projects.splice(i, 1);
        tr.remove();
        $('#cfg-proj-count').textContent = `(${CFG.projects.projects.length} 项)`;
        toast(`已移除 ${p.name}（点「保存项目表」生效）`);
    };
    // 浏览选择目录
    const pathInput = tr.querySelector('[data-f=path]');
    tr.querySelector('.fs-pick-btn').onclick = () => openFsPicker(p.source?.path || '', v => {
        p.source = p.source || {}; p.source.type = 'local'; p.source.path = v;
        pathInput.value = v;
    });
    // 探测
    tr.querySelector('.cfg-detect-btn').onclick = async () => {
        const path = (p.source?.path || '').trim();
        if (!path) { toast('先填本地路径再探测', 'warn'); return; }
        try {
            const r = await dashJson('/api/config/detect', { method: 'POST', body: JSON.stringify({ path }) });
            if (!r.ok) { toast('探测失败: ' + r.msg, 'err'); return; }
            p.build = p.build || {};
            p.build.system = r.system;
            if (r.test_dir) p.build.test_dir = r.test_dir;
            tr.querySelector('[data-f=build_system]').value = r.system;
            toast(`🔍 ${r.name_guess}: ${r.system}${r.test_dir ? ' · ' + r.test_dir : ''} (${(r.found || []).join(', ')})`);
        } catch (e) { toast('探测失败: ' + e.message, 'err'); }
    };
    return tr;
}

async function saveGlobalCfg() {
    if (!CFG) return;
    CFG.config.mcp_url = $('#cfg-mcp-url').value.trim();
    CFG.config.github = CFG.config.github || {}; CFG.config.github.org = $('#cfg-org').value.trim();
    CFG.config.server = CFG.config.server || {}; CFG.config.server.port = parseInt($('#cfg-port').value, 10) || 8765;
    CFG.config.sync = CFG.config.sync || {}; CFG.config.sync.concurrency = Math.max(1, parseInt($('#cfg-concurrency').value, 10) || 1);
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
    // 目录浏览对话框
    $('#fs-cancel').onclick = fsClose;
    $('#fs-confirm').onclick = () => { if (fsPick.cb && fsPick.path) fsPick.cb(fsPick.path); fsClose(); };
    $('#fs-home').onclick = () => fsLoad('~');
    $('#fs-up').onclick = () => fsLoad((fsPick.path || '/') + '/..');
    $('#fs-show-hidden').onchange = e => { fsPick.showHidden = e.target.checked; localStorage.setItem('utie-fs-hidden', fsPick.showHidden ? '1' : '0'); renderFsList(); };
    $('#fs-path-input').onkeydown = e => { if (e.key === 'Enter') fsLoad(e.target.value); };
    $('#fs-picker-modal').addEventListener('click', e => { if (e.target.id === 'fs-picker-modal') fsClose(); });
}

// ═══ 目录浏览对话框 ═══
const fsPick = { path: '', cb: null, entries: [], showHidden: localStorage.getItem('utie-fs-hidden') === '1' };

async function openFsPicker(startPath, onPick) {
    if (!S.dash.server) { toast('目录浏览需要伴随服务运行中', 'warn'); return; }
    fsPick.cb = onPick;
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
    const shown = fsPick.entries.filter(e => fsPick.showHidden || !e.name.startsWith('.'));
    if (!shown.length) { list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">空目录</div>'; return; }
    shown.forEach(e => {
        const row = document.createElement('div');
        row.className = 'fs-row' + (e.dir ? '' : ' fs-file');
        row.textContent = (e.dir ? '📁 ' : '📄 ') + e.name + (e.symlink ? ' ↗' : '');
        if (e.dir) row.onclick = () => fsLoad(fsPick.path + '/' + e.name);
        list.appendChild(row);
    });
}
