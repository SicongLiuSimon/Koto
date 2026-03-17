/**
 * Koto Skill Marketplace — Frontend Logic
 * ==========================================
 * GitHub Extension Marketplace 风格的 Skill 管理界面
 */

const API = {
  base: '/api/skillmarket',
  catalog:       () => `${API.base}/catalog`,
  library:       () => `${API.base}/library`,
  featured:      () => `${API.base}/featured`,
  search:        (q)  => `${API.base}/search?q=${encodeURIComponent(q)}`,
  autoBuild:     () => `${API.base}/auto-build`,
  previewPrompt: () => `${API.base}/preview-prompt`,
  fromSession:   () => `${API.base}/from-session`,
  sessions:      () => `${API.base}/sessions`,
  active:        () => `${API.base}/active`,
  install:       () => `${API.base}/install`,
  uninstall:     (id) => `${API.base}/uninstall/${id}`,
  toggle:        (id) => `${API.base}/toggle/${id}`,
  edit:          (id) => `${API.base}/edit/${id}`,
  duplicate:     (id) => `${API.base}/duplicate/${id}`,
  exportOne:     (id) => `${API.base}/export/${id}`,
  exportPack:    (ids) => `${API.base}/export-pack?${ids.map(i=>`ids[]=${i}`).join('&')}`,
  importPack:    () => `${API.base}/import`,
  rate:          (id) => `${API.base}/rate/${id}`,
  stats:         () => `${API.base}/stats`,
};

/* ═══════════════ State ═══════════════ */
const state = {
  currentTab: 'catalog',    // catalog | library | studio | import-export
  activeCategory: 'all',
  activeNature: 'all',      // all | model_hint | domain_skill | system
  searchQuery: '',
  allSkills: [],
  filteredSkills: [],
  sortBy: 'name',
  selectedSkills: new Set(),  // for batch export
  stats: {},
};

/* ═══════════════ DOM Helpers ═════════════ */
function $(sel, ctx = document) { return ctx.querySelector(sel); }
function $$(sel, ctx = document) { return [...ctx.querySelectorAll(sel)]; }

function toast(msg, type = 'info', duration = 3500) {
  const container = document.getElementById('sm-toast-container');
  const el = document.createElement('div');
  const icons = { success: '✅', error: '❌', info: 'ℹ️' };
  el.className = `sm-toast ${type}`;
  el.innerHTML = `<span>${icons[type] || 'ℹ️'}</span> <span>${msg}</span>`;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

async function api(method, url, body = null, isForm = false) {
  const opts = { method, headers: {} };
  if (body) {
    if (isForm) {
      opts.body = body;  // FormData
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }
  const res = await fetch(url, opts);
  if (!res.ok && res.status !== 409) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json();
}

/* ═══════════════ Tab Navigation ═════════════ */
function switchTab(tabName) {
  state.currentTab = tabName;
  $$('.sm-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
  $$('.sm-panel').forEach(p => p.classList.toggle('hidden', p.id !== `panel-${tabName}`));

  if (tabName === 'catalog') loadCatalog();
  if (tabName === 'library') loadLibrary();
  if (tabName === 'import-export') loadImportExport();
  if (tabName === 'stats') loadStats();
}

/* ═══════════════ Catalog ═════════════════ */
async function loadCatalog(category = null) {
  if (category !== null) state.activeCategory = category;
  showSkeletons('catalog-grid');

  try {
    let data;
    if (state.searchQuery) {
      data = await api('GET', API.search(state.searchQuery));
    } else {
      let url = API.catalog();
      const params = [];
      if (state.activeCategory && state.activeCategory !== 'all') {
        params.push(`category=${state.activeCategory}`);
      }
      if (state.activeNature && state.activeNature !== 'all') {
        params.push(`skill_nature=${state.activeNature}`);
      }
      if (params.length) url += '?' + params.join('&');
      data = await api('GET', url);
    }

    const skills = data.skills || [];
    state.allSkills = skills;
    state.filteredSkills = sortSkills(skills);
    renderSkillGrid('catalog-grid', state.filteredSkills);
    updateResultCount(state.filteredSkills.length, 'catalog');

    // Update sidebar counts
    updateSidebarCounts(skills);
    updateActiveBar(skills);
  } catch (e) {
    showError('catalog-grid', e.message);
    toast(e.message, 'error');
  }
}

function updateSidebarCounts(skills) {
  // Category counts
  const catCounts = { all: skills.length, agent: 0, behavior: 0, style: 0, domain: 0, workflow: 0, custom: 0 };
  skills.forEach(s => { catCounts[s.category] = (catCounts[s.category] || 0) + 1; });
  Object.entries(catCounts).forEach(([cat, count]) => {
    const el = document.querySelector(`[data-cat="${cat}"] .item-count`);
    if (el) el.textContent = count;
  });

  // Nature counts
  const natureCounts = { all: skills.length, model_hint: 0, domain_skill: 0, system: 0 };
  skills.forEach(s => { natureCounts[s.skill_nature] = (natureCounts[s.skill_nature] || 0) + 1; });
  Object.entries(natureCounts).forEach(([nat, count]) => {
    const el = document.querySelector(`[data-nature-count="${nat}"]`);
    if (el) el.textContent = count;
  });
}

function updateActiveBar(skills) {
  const enabled = skills.filter(s => s.enabled && s.id !== 'long_term_memory');
  const bar = document.getElementById('active-count-bar');
  if (!bar) return;
  const pillsEl = document.getElementById('active-pills');
  if (!pillsEl) return;

  if (!enabled.length) {
    pillsEl.innerHTML = '<span class="no-active-msg">暂无激活的 Skill — 在下方卡片中点击「启用」开始体验</span>';
    return;
  }

  pillsEl.innerHTML = enabled.map(s => `
    <span class="active-pill" title="点击禁用：${escHtml(s.name)}" data-id="${s.id}">
      ${escHtml(s.icon || '🔧')} ${escHtml(s.name)}
      <span class="pill-x" data-id="${s.id}" title="禁用">×</span>
    </span>
  `).join('');

  // bind pill × to disable
  pillsEl.querySelectorAll('.pill-x').forEach(x => {
    x.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSkill(x.dataset.id, false);
    });
  });
}

/* ═══════════════ Library ═════════════════ */
async function loadLibrary() {
  showSkeletons('library-grid');
  try {
    const data = await api('GET', API.library());
    renderSkillGrid('library-grid', data.skills || []);
    updateResultCount(data.total || 0, 'library');
  } catch (e) {
    showError('library-grid', e.message);
  }
}

/* ═══════════════ Card Rendering ══════════ */
function sortSkills(skills) {
  const copy = [...skills];
  switch (state.sortBy) {
    case 'name':    return copy.sort((a, b) => a.name.localeCompare(b.name));
    case 'rating':  return copy.sort((a, b) => (b.rating || 0) - (a.rating || 0));
    case 'enabled': return copy.sort((a, b) => (b.enabled ? 1 : 0) - (a.enabled ? 1 : 0));
    default:        return copy;
  }
}

function renderSkillGrid(gridId, skills) {
  const grid = document.getElementById(gridId);
  if (!grid) return;

  if (!skills.length) {
    grid.innerHTML = `
      <div class="sm-empty" style="grid-column:1/-1">
        <div class="empty-icon">🔍</div>
        <h3>没有找到匹配的 Skill</h3>
        <p>尝试更换搜索词或分类过滤，或去 Studio 创建一个新的 Skill</p>
      </div>`;
    return;
  }

  grid.innerHTML = skills.map(skill => renderSkillCard(skill)).join('');

  // Bind click events
  grid.querySelectorAll('.sm-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.btn')) return;  // 不触发 drawer
      openDrawer(card.dataset.skillId);
    });
  });

  grid.querySelectorAll('[data-action="toggle"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleSkill(btn.dataset.id, btn.dataset.enabled !== 'true');
    });
  });

  grid.querySelectorAll('[data-action="edit"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      openEditModal(btn.dataset.id);
    });
  });
}

function renderSkillCard(skill) {
  const catClass = `category-${skill.category}`;
  const catLabel = { agent: '🤖 Agent 能力', behavior: '⚙️ 行为', style: '🎨 风格', domain: '🔬 领域', workflow: '⚡ 工作流', memory: '🧠 记忆', custom: '✨ 自定义' };
  const natureLabel = { model_hint: '🧠 通用能力', domain_skill: '🎯 任务技能', system: '⚙️ 系统' };
  const natureClass = skill.skill_nature ? `nature-${skill.skill_nature}` : '';
  const natureBadge = (skill.skill_nature && skill.skill_nature !== 'system')
    ? `<span class="sm-tag ${natureClass}">${natureLabel[skill.skill_nature] || skill.skill_nature}</span>`
    : '';
  const stars = renderStarsMini(skill.rating || 0, skill.rating_count || 0);
  const enabledBadge = skill.enabled
    ? `<span class="sm-badge enabled">● 已启用</span>`
    : `<span class="sm-badge disabled">○ 已禁用</span>`;
  const builtinBadge = skill.is_builtin ? `<span class="sm-badge builtin">内置</span>` : '';

  const editBtn = !skill.is_builtin
    ? `<button class="btn-gear" data-action="edit" data-id="${skill.id}" title="编辑技能">⚙</button>`
    : '';

  return `
  <div class="sm-card ${skill.enabled ? 'enabled' : ''}" data-skill-id="${skill.id}">
    <div class="sm-card-header">
      <div class="sm-card-icon">${skill.icon || '🔧'}</div>
      <div class="sm-card-meta">
        <div class="sm-card-name">${escHtml(skill.name)}</div>
        <div class="sm-card-author">
          <span class="author-name">${escHtml(skill.author || 'builtin')}</span>
          &nbsp;·&nbsp; v${escHtml(skill.version || '1.0.0')}
        </div>
      </div>
      ${editBtn}
    </div>
    <div class="sm-card-desc">${escHtml(skill.description || '（暂无描述）')}</div>
    <div class="sm-card-footer">
      <span class="sm-tag ${catClass}">${catLabel[skill.category] || skill.category}</span>
      ${natureBadge}
      ${(skill.tags || []).slice(0, 2).map(t => `<span class="sm-tag">${escHtml(t)}</span>`).join('')}
      ${stars}
    </div>
    <div class="sm-card-actions" style="margin-top:4px">
      ${enabledBadge}
      ${builtinBadge}
      <button class="btn btn-sm ${skill.enabled ? 'btn-secondary' : 'btn-primary'} ml-auto"
        data-action="toggle" data-id="${skill.id}" data-enabled="${skill.enabled}"
        style="margin-left:auto">
        ${skill.enabled ? '禁用' : '启用'}
      </button>
    </div>
  </div>`;
}

function renderStarsMini(avg, count) {
  if (!count) return '';
  const full = Math.round(avg);
  const stars = Array.from({ length: 5 }, (_, i) =>
    `<span style="color:${i < full ? '#e3b341' : '#30363d'}">★</span>`
  ).join('');
  return `<span class="sm-rating" style="margin-left:auto">${stars} <span style="color:var(--text-muted);font-size:11px">(${count})</span></span>`;
}

/* ═══════════════ Drawer ═════════════════ */
async function openDrawer(skillId) {
  try {
    const data = await api('GET', `/api/skills/${skillId}`);
    const skill = data.skill;

    const drawer = document.getElementById('sm-drawer');
    const overlay = document.getElementById('sm-drawer-overlay');

    // Get rating
    const ratingsData = await api('GET', API.stats()).catch(() => ({}));

    $('#drawer-icon', drawer).textContent = skill.icon || '🔧';
    $('#drawer-name', drawer).textContent = skill.name;
    $('#drawer-sub', drawer).textContent  = `${skill.author || 'builtin'} · v${skill.version || '1.0.0'} · ${skill.category}`;
    $('#drawer-desc', drawer).textContent  = skill.description || '（暂无描述）';
    $('#drawer-intent', drawer).textContent = skill.intent_description || '（未设置）';

    const prompt = skill.system_prompt_template || skill.prompt || '（未设置）';
    $('#drawer-prompt', drawer).textContent = prompt;

    // Meta grid
    const catLabel = { behavior: '⚙️ 行为', style: '🎨 风格', domain: '🔬 领域', custom: '✨ 自定义' };
    ['drawer-meta-category', 'drawer-meta-enabled', 'drawer-meta-author', 'drawer-meta-version'].forEach(id => {
      const el = $(`#${id}`, drawer);
      if (!el) return;
    });
    const metaEl = $('#drawer-meta', drawer);
    if (metaEl) {
      metaEl.innerHTML = `
        <div class="sm-meta-item"><div class="meta-label">分类</div><div class="meta-value">${catLabel[skill.category] || skill.category}</div></div>
        <div class="sm-meta-item"><div class="meta-label">状态</div><div class="meta-value">${skill.enabled ? '✅ 已启用' : '⏸️ 已禁用'}</div></div>
        <div class="sm-meta-item"><div class="meta-label">作者</div><div class="meta-value">${escHtml(skill.author || 'builtin')}</div></div>
        <div class="sm-meta-item"><div class="meta-label">版本</div><div class="meta-value">v${skill.version || '1.0.0'}</div></div>
        <div class="sm-meta-item"><div class="meta-label">任务类型</div><div class="meta-value">${(skill.task_types || []).join(', ') || '通用'}</div></div>
        <div class="sm-meta-item"><div class="meta-label">创建时间</div><div class="meta-value">${skill.created_at ? skill.created_at.slice(0,10) : '—'}</div></div>
      `;
    }

    // Tags
    const tagsEl = $('#drawer-tags', drawer);
    if (tagsEl) {
      tagsEl.innerHTML = (skill.tags || []).map(t => `<span class="sm-tag">${escHtml(t)}</span>`).join('') || '<span class="text-muted">无标签</span>';
    }

    // Input variables
    const varsEl = $('#drawer-variables', drawer);
    if (varsEl) {
      const vars = skill.input_variables || [];
      varsEl.innerHTML = vars.length
        ? vars.map(v => `<div style="background:var(--bg-base);border:1px solid var(--border);border-radius:4px;padding:8px 12px;font-size:12px;font-family:monospace;margin-bottom:6px">
            <span style="color:#e3b341">{${v.name}}</span>
            <span style="color:var(--text-muted)"> · ${v.type || 'string'} · ${v.required ? '必填' : '可选'}</span>
            ${v.description ? `<br><span style="color:var(--text-muted);font-family:sans-serif">${escHtml(v.description)}</span>` : ''}
          </div>`).join('')
        : '<span class="text-muted text-sm">无（直接注入 prompt）</span>';
    }

    // Footer buttons
    const footerEl = $('#drawer-footer', drawer);
    const isBuiltin = skill.author === 'builtin';
    if (footerEl) {
      footerEl.innerHTML = `
        <button class="btn btn-primary" onclick="toggleSkill('${skill.id}', ${!skill.enabled})" id="drawer-toggle-btn">
          ${skill.enabled ? '禁用' : '启用'}
        </button>
        ${!isBuiltin ? `<button class="btn btn-secondary btn-gear-label" onclick="openEditModal('${skill.id}')" title="编辑 Skill"><span class="gear-icon">⚙</span> 编辑</button>` : ''}
        <button class="btn btn-secondary" onclick="duplicateSkill('${skill.id}')">🔁 克隆</button>
        <button class="btn btn-secondary" onclick="exportOneSkill('${skill.id}')">⬇️ 导出</button>
        ${!isBuiltin ? `<button class="btn btn-danger" onclick="uninstallSkill('${skill.id}')">🗑️ 卸载</button>` : ''}
        <div style="margin-left:auto;display:flex;align-items:center;gap:6px">
          <span style="font-size:12px;color:var(--text-muted)">评分：</span>
          <div class="sm-stars" onmouseleave="resetStarHover()" id="drawer-stars">
            ${[1,2,3,4,5].map(s => `<span class="star" data-score="${s}" onclick="rateSkill('${skill.id}',${s})" onmouseenter="hoverStar(this)">★</span>`).join('')}
          </div>
        </div>
      `;
    }

    // Store current skill id
    drawer.dataset.skillId = skillId;

    overlay.classList.add('open');
    drawer.classList.add('open');
  } catch (e) {
    toast(`加载 Skill 详情失败: ${e.message}`, 'error');
  }
}

function closeDrawer() {
  document.getElementById('sm-drawer').classList.remove('open');
  document.getElementById('sm-drawer-overlay').classList.remove('open');
}

/* ═══════════════ Edit Modal ═════════════════ */
async function openEditModal(skillId) {
  const modal = document.getElementById('skill-edit-modal');
  const overlay = document.getElementById('skill-edit-overlay');
  if (!modal || !overlay) return;

  try {
    const data = await api('GET', `/api/skills/${skillId}`);
    const skill = data.skill;

    document.getElementById('edit-modal-title').textContent = `⚙ 编辑：${skill.name}`;
    document.getElementById('edit-skill-id').value = skill.id;
    document.getElementById('edit-skill-name').value = skill.name || '';
    document.getElementById('edit-skill-icon').value = skill.icon || '🔧';
    document.getElementById('edit-skill-description').value = skill.description || '';
    document.getElementById('edit-skill-prompt').value = skill.system_prompt_template || skill.prompt || '';
    document.getElementById('edit-skill-intent').value = skill.intent_description || '';
    document.getElementById('edit-skill-tags').value = (skill.tags || []).join(', ');

    overlay.classList.add('open');
    modal.classList.add('open');
    document.getElementById('edit-skill-name').focus();
  } catch (e) {
    toast(`加载 Skill 失败: ${e.message}`, 'error');
  }
}

function closeEditModal() {
  document.getElementById('skill-edit-modal')?.classList.remove('open');
  document.getElementById('skill-edit-overlay')?.classList.remove('open');
}

async function saveSkillEdit() {
  const skillId = document.getElementById('edit-skill-id').value;
  const name = document.getElementById('edit-skill-name').value.trim();
  const icon = document.getElementById('edit-skill-icon').value.trim();
  const description = document.getElementById('edit-skill-description').value.trim();
  const prompt = document.getElementById('edit-skill-prompt').value.trim();
  const intent = document.getElementById('edit-skill-intent').value.trim();
  const tagsRaw = document.getElementById('edit-skill-tags').value.trim();
  const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];

  if (!name) { toast('技能名称不能为空', 'error'); return; }

  const saveBtn = document.getElementById('edit-save-btn');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = '保存中…'; }

  try {
    await api('PUT', API.edit(skillId), {
      name, icon, description,
      system_prompt_template: prompt,
      intent_description: intent,
      tags,
    });
    toast(`✅ 技能「${name}」已更新`, 'success');
    closeEditModal();
    // Refresh views
    loadCatalog();
    loadLibrary();
    // Refresh drawer if open
    const drawer = document.getElementById('sm-drawer');
    if (drawer?.classList.contains('open') && drawer.dataset.skillId === skillId) {
      openDrawer(skillId);
    }
  } catch (e) {
    toast(`保存失败: ${e.message}`, 'error');
  } finally {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = '保存更改'; }
  }
}

function hoverStar(starEl) {
  const score = parseInt(starEl.dataset.score);
  const stars = starEl.closest('#drawer-stars').querySelectorAll('.star');
  stars.forEach((s, i) => s.classList.toggle('filled', i < score));
}

function resetStarHover() {
  const stars = document.querySelectorAll('#drawer-stars .star');
  stars.forEach(s => s.classList.remove('filled'));
}

async function rateSkill(skillId, score) {
  try {
    const data = await api('POST', API.rate(skillId), { score });
    toast(`已评 ${score} 星！平均：${data.avg} ★`, 'success');
  } catch (e) {
    toast(`评分失败: ${e.message}`, 'error');
  }
}

/* ═══════════════ Toggle / Install / Uninstall ════════════ */
async function toggleSkill(skillId, enable) {
  try {
    const data = await api('POST', API.toggle(skillId), { enabled: enable });
    toast(`${enable ? '✅ 已启用' : '⏸️ 已禁用'} ${skillId}`, 'success');
    // Refresh
    if (state.currentTab === 'catalog') loadCatalog();
    else if (state.currentTab === 'library') loadLibrary();
    // Update drawer if open
    const drawer = document.getElementById('sm-drawer');
    if (drawer.classList.contains('open') && drawer.dataset.skillId === skillId) {
      openDrawer(skillId);
    }
  } catch (e) {
    toast(`操作失败: ${e.message}`, 'error');
  }
}

async function uninstallSkill(skillId) {
  if (!confirm(`确定要卸载 Skill「${skillId}」吗？此操作不可撤销。`)) return;
  try {
    await api('POST', API.uninstall(skillId));
    toast(`已卸载 ${skillId}`, 'success');
    closeDrawer();
    loadLibrary();
    loadCatalog();
  } catch (e) {
    toast(`卸载失败: ${e.message}`, 'error');
  }
}

async function duplicateSkill(skillId) {
  const newName = prompt('新技能名称（留空使用默认副本名）:', '');
  try {
    const body = newName ? { new_name: newName } : {};
    const data = await api('POST', API.duplicate(skillId), body);
    toast(`已克隆 → ${data.new_skill_id}`, 'success');
    loadCatalog();
    loadLibrary();
  } catch (e) {
    toast(`克隆失败: ${e.message}`, 'error');
  }
}

function exportOneSkill(skillId) {
  window.location.href = API.exportOne(skillId);
  toast('正在下载 .kotosk 文件…', 'info');
}

/* ═══════════════ Studio ═════════════════ */
let previewDebounceTimer = null;
let studioSourceMode = 'description'; // description | session

function initStudio() {
  // Source mode toggle
  $$('.source-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      studioSourceMode = tab.dataset.mode;
      $$('.source-tab').forEach(t => t.classList.toggle('active', t.dataset.mode === studioSourceMode));
      $('#source-description').classList.toggle('hidden', studioSourceMode !== 'description');
      $('#source-session').classList.toggle('hidden', studioSourceMode !== 'session');
      // Auto-load session list when switching to session mode
      if (studioSourceMode === 'session') loadSessionList();
    });
  });

  // Sliders → live preview
  const sliders = $$('.style-slider');
  sliders.forEach(slider => {
    const valEl = document.getElementById(`val-${slider.id}`);
    slider.addEventListener('input', () => {
      if (valEl) valEl.textContent = parseFloat(slider.value).toFixed(1);
      schedulePreview();
    });
  });

  // Name / description → preview
  ['studio-name', 'studio-description'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', schedulePreview);
  });

  $('#studio-build-btn') && $('#studio-build-btn').addEventListener('click', buildSkill);
  $('#studio-session-btn') && $('#studio-session-btn').addEventListener('click', buildFromSession);
}

function schedulePreview() {
  clearTimeout(previewDebounceTimer);
  previewDebounceTimer = setTimeout(fetchPreview, 600);
}

async function fetchPreview() {
  const name = (document.getElementById('studio-name')?.value || '').trim();
  const desc = (document.getElementById('studio-description')?.value || '').trim();
  if (!name && !desc) return;

  const body = buildStyleBody({ name: name || '未命名', description: desc });
  try {
    const data = await api('POST', API.previewPrompt(), body);
    const el = document.getElementById('prompt-preview');
    if (el) {
      el.classList.remove('prompt-preview-placeholder');
      el.textContent = data.system_prompt || '（无内容）';
    }
    // Update suggested id
    const idEl = document.getElementById('studio-suggested-id');
    if (idEl) idEl.textContent = data.suggested_id || '';
  } catch (e) {
    // ignore preview errors
  }
}

function buildStyleBody(extra = {}) {
  const getSlider = (id) => parseFloat(document.getElementById(id)?.value || 0.5);
  const getDomain = () => document.getElementById('studio-domain')?.value || 'general';
  return {
    formality:   getSlider('slider-formality'),
    verbosity:   getSlider('slider-verbosity'),
    empathy:     getSlider('slider-empathy'),
    structure:   getSlider('slider-structure'),
    creativity:  getSlider('slider-creativity'),
    positivity:  getSlider('slider-positivity'),
    proactivity: getSlider('slider-proactivity'),
    humor:       getSlider('slider-humor'),
    domain:      getDomain(),
    ...extra,
  };
}

async function buildSkill() {
  const name = (document.getElementById('studio-name')?.value || '').trim();
  const desc = (document.getElementById('studio-description')?.value || '').trim();
  const icon = (document.getElementById('studio-icon')?.value || '🎭').trim();
  const category = document.getElementById('studio-category')?.value || 'style';
  const enabled = document.getElementById('studio-enabled')?.checked || false;

  if (!name) { toast('请填写技能名称', 'error'); return; }
  if (!desc) { toast('请填写风格描述', 'error'); return; }

  const btn = document.getElementById('studio-build-btn');
  btn.disabled = true;
  btn.textContent = '⏳ 生成中…';

  try {
    const body = buildStyleBody({ name, description: desc, icon, category, enabled, save: true });
    const data = await api('POST', API.autoBuild(), body);
    toast(`✅ 技能「${data.skill.name}」已创建！ID: ${data.skill_id}`, 'success', 5000);
    // Reset form
    document.getElementById('studio-name').value = '';
    document.getElementById('studio-description').value = '';
    document.getElementById('prompt-preview').textContent = '';
    document.getElementById('prompt-preview').classList.add('prompt-preview-placeholder');
    document.getElementById('prompt-preview').textContent = '在上方填写技能信息后，这里将实时预览生成的系统 Prompt…';
    loadLibrary();
  } catch (e) {
    if (e.message.includes('已存在')) {
      if (confirm(`技能已存在，是否覆盖？`)) {
        const body = buildStyleBody({ name, description: desc, icon, category, enabled, save: true, overwrite: true });
        await api('POST', API.autoBuild(), body);
        toast('✅ 已覆盖更新', 'success');
        loadLibrary();
      }
    } else {
      toast(`创建失败: ${e.message}`, 'error');
    }
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ 生成 Skill';
  }
}

/* ═══════════════ Session picker ═════════════ */
let selectedSessionId = null;

async function loadSessionList() {
  const listEl = document.getElementById('session-picker-list');
  if (!listEl) return;
  listEl.innerHTML = '<div class="session-loading">🔄 正在加载对话列表…</div>';
  try {
    const data = await api('GET', API.sessions());
    const sessions = data.sessions || [];
    if (!sessions.length) {
      listEl.innerHTML = '<div class="session-empty">暂无对话记录。先去和 Koto 聊几条消息吧。</div>';
      return;
    }
    listEl.innerHTML = sessions.map(s => `
      <div class="session-item" data-id="${escHtml(s.session_id)}" onclick="selectSession('${escHtml(s.session_id)}', this)">
        <div class="session-item-icon">💬</div>
        <div class="session-item-body">
          <div class="session-item-title">${escHtml(s.title)}</div>
          <div class="session-item-meta">${s.message_count} 条消息 · ${escHtml(s.updated_at)}</div>
        </div>
        <span class="session-item-check">✓</span>
      </div>
    `).join('');
  } catch (e) {
    listEl.innerHTML = `<div class="session-empty">加载失败：${escHtml(e.message)}</div>`;
  }
}

function selectSession(sessionId, el) {
  selectedSessionId = sessionId;
  document.querySelectorAll('#session-picker-list .session-item').forEach(i => i.classList.remove('selected'));
  if (el) el.classList.add('selected');
  // Mirror to text input
  const inp = document.getElementById('studio-session-id');
  if (inp) inp.value = sessionId;
  // Advance step indicator
  setWizardStep(2);
}

function setWizardStep(active) {
  [1, 2, 3].forEach(n => {
    const el = document.getElementById(`wstep-${n}`);
    if (!el) return;
    el.classList.toggle('active', n === active);
    el.classList.toggle('done', n < active);
  });
}

async function buildFromSession() {
  const sessionId = (document.getElementById('studio-session-id')?.value || selectedSessionId || '').trim();
  const name = (document.getElementById('studio-session-name')?.value || '').trim();
  const desc = (document.getElementById('studio-session-desc')?.value || '').trim();
  const icon = (document.getElementById('studio-session-icon')?.value || '💬').trim();
  const enableAfter = document.getElementById('studio-session-enabled')?.checked ?? true;

  if (!sessionId) { toast('请先选择一段对话或输入 Session ID', 'error'); return; }
  if (!name) { toast('请填写技能名称', 'error'); return; }

  const btn = document.getElementById('studio-session-btn');
  const resultEl = document.getElementById('session-extract-result');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 分析中…'; }
  if (resultEl) {
    resultEl.className = 'session-extracting';
    resultEl.innerHTML = '<span class="spinner">⏳</span> 正在分析对话风格，自动提取行为模式…';
  }
  setWizardStep(3);

  try {
    const data = await api('POST', API.fromSession(), {
      session_id: sessionId, name, description: desc, icon,
      save: true, enabled: enableAfter,
    });

    const prompt = data.skill?.system_prompt_template || data.skill?.prompt || '（提取完成，但 Prompt 为空）';
    if (resultEl) {
      resultEl.className = 'session-extract-result';
      resultEl.textContent = prompt;
    }

    const enableMsg = enableAfter ? ' 已自动启用，下次对话即可感受效果。' : '';
    toast(`✅ 技能「${data.skill.name}」已创建！${enableMsg}`, 'success', 6000);
    loadLibrary();
    if (enableAfter) loadCatalog();
  } catch (e) {
    if (resultEl) {
      resultEl.className = 'session-extract-placeholder';
      resultEl.textContent = `提取失败：${e.message}`;
    }
    toast(`提取失败: ${e.message}`, 'error');
    setWizardStep(1);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '💬 提取技能'; }
  }
}

/* ═══════════════ Import / Export ═════════════ */
function loadImportExport() {
  // Build export checklist
  loadExportChecklist();
}

async function loadExportChecklist() {
  const container = document.getElementById('export-checklist');
  if (!container) return;

  try {
    const data = await api('GET', API.catalog());
    const skills = data.skills || [];
    state.selectedSkills = new Set();

    container.innerHTML = skills.map(s => `
      <label class="export-check-item">
        <input type="checkbox" value="${s.id}" onchange="toggleExportSelect('${s.id}', this.checked)">
        <span class="check-icon">${s.icon || '🔧'}</span>
        <span class="check-name">${escHtml(s.name)}</span>
        <span class="check-category">${s.category}</span>
      </label>
    `).join('');
  } catch (e) {
    toast('加载技能列表失败', 'error');
  }
}

function toggleExportSelect(id, checked) {
  if (checked) state.selectedSkills.add(id);
  else state.selectedSkills.delete(id);
  const btn = document.getElementById('export-pack-btn');
  if (btn) btn.textContent = `⬇️ 打包导出 (${state.selectedSkills.size})`;
}

function exportSelectedPack() {
  if (!state.selectedSkills.size) { toast('请先选择至少一个 Skill', 'error'); return; }
  const packName = document.getElementById('export-pack-name')?.value || 'my-skill-pack';
  const ids = [...state.selectedSkills];
  const url = API.exportPack(ids) + `&pack_name=${encodeURIComponent(packName)}`;
  window.location.href = url;
  toast(`正在下载包含 ${ids.length} 个 Skill 的 .kotosk 包…`, 'info');
}

function initDropZone() {
  const zone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('import-file-input');
  if (!zone || !fileInput) return;

  zone.addEventListener('click', () => fileInput.click());
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) handleImportFile(file);
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) handleImportFile(fileInput.files[0]);
  });
}

async function handleImportFile(file) {
  if (!file.name.endsWith('.kotosk')) { toast('仅支持 .kotosk 文件', 'error'); return; }

  const overwrite = document.getElementById('import-overwrite')?.checked || false;
  const formData = new FormData();
  formData.append('file', file);
  if (overwrite) formData.append('overwrite', 'true');

  const zone = document.getElementById('drop-zone');
  zone.innerHTML = `<div class="drop-icon">⏳</div><p>正在导入 ${escHtml(file.name)}…</p>`;

  try {
    const data = await api('POST', API.importPack(), formData, true);
    const msg = `导入成功：${data.installed.length} 个 Skill | 跳过: ${data.skipped.length} | 错误: ${data.errors.length}`;
    toast(msg, data.errors.length ? 'info' : 'success', 6000);
    loadCatalog();
    loadLibrary();

    // Show result in drop zone
    zone.innerHTML = `
      <div class="drop-icon">✅</div>
      <p>导入完成！${msg}</p>
      <p style="margin-top:8px;font-size:11px;color:var(--text-muted)">已安装: ${data.installed.join(', ') || '无'}</p>
    `;
  } catch (e) {
    toast(`导入失败: ${e.message}`, 'error');
    zone.innerHTML = `<div class="drop-icon">📦</div><p>拖入 .kotosk 文件，或点击选择文件</p><p style="margin-top:8px;font-size:12px;color:var(--text-muted)">支持批量 Skill 包导入</p>`;
  }
}

/* ═══════════════ Stats Panel ═════════════ */
async function loadStats() {
  try {
    const data = await api('GET', API.stats());
    const el = document.getElementById('stats-content');
    if (!el) return;

    const catColors = { behavior: '#58a6ff', style: '#a371f7', domain: '#e3b341', custom: '#3fb950' };
    const catBars = Object.entries(data.by_category || {}).map(([cat, count]) => `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span style="width:60px;font-size:12px;color:var(--text-muted)">${cat}</span>
        <div style="flex:1;background:var(--bg-elevated);border-radius:4px;height:18px;overflow:hidden">
          <div style="width:${Math.round(count/data.total_skills*100)}%;background:${catColors[cat]||'#8b949e'};height:100%;border-radius:4px;transition:width 0.5s"></div>
        </div>
        <span style="font-size:12px;color:var(--text-muted);width:20px;text-align:right">${count}</span>
      </div>
    `).join('');

    el.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px">
        <div class="sm-meta-item"><div class="meta-label">总 Skill 数</div><div class="meta-value" style="font-size:22px">${data.total_skills}</div></div>
        <div class="sm-meta-item"><div class="meta-label">当前启用</div><div class="meta-value" style="font-size:22px;color:var(--accent-green)">${data.enabled_skills}</div></div>
        <div class="sm-meta-item"><div class="meta-label">自定义</div><div class="meta-value" style="font-size:22px;color:#a371f7">${data.custom_skills}</div></div>
        <div class="sm-meta-item"><div class="meta-label">内置</div><div class="meta-value" style="font-size:22px;color:#58a6ff">${data.builtin_skills}</div></div>
        <div class="sm-meta-item"><div class="meta-label">平均评分</div><div class="meta-value" style="font-size:22px;color:#e3b341">${data.avg_rating ? '★ ' + data.avg_rating : '—'}</div></div>
        <div class="sm-meta-item"><div class="meta-label">已评分 Skill</div><div class="meta-value" style="font-size:22px">${data.rated_count}</div></div>
      </div>
      <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);margin-bottom:10px">分类分布</h3>
      ${catBars}
    `;
  } catch (e) {
    toast(`加载统计失败: ${e.message}`, 'error');
  }
}

/* ═══════════════ Search ═══════════════ */
let searchDebounce = null;
function onSearchInput(e) {
  state.searchQuery = e.target.value.trim();
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    if (state.currentTab === 'catalog') loadCatalog();
  }, 400);
}

/* ═══════════════ Skeleton / Error Helpers ═════════════ */
function showSkeletons(gridId, count = 6) {
  const grid = document.getElementById(gridId);
  if (!grid) return;
  grid.innerHTML = Array.from({ length: count }, () => `
    <div class="skeleton-card">
      <div style="display:flex;gap:12px">
        <div class="skeleton" style="width:48px;height:48px;border-radius:10px;flex-shrink:0"></div>
        <div style="flex:1">
          <div class="skeleton" style="height:14px;width:70%;margin-bottom:8px"></div>
          <div class="skeleton" style="height:11px;width:40%"></div>
        </div>
      </div>
      <div class="skeleton" style="height:12px;width:90%;margin-top:4px"></div>
      <div class="skeleton" style="height:12px;width:60%"></div>
      <div style="display:flex;gap:8px;margin-top:4px">
        <div class="skeleton" style="height:22px;width:60px;border-radius:20px"></div>
        <div class="skeleton" style="height:22px;width:50px;border-radius:20px"></div>
      </div>
    </div>
  `).join('');
}

function showError(gridId, msg) {
  const grid = document.getElementById(gridId);
  if (!grid) return;
  grid.innerHTML = `
    <div class="sm-empty" style="grid-column:1/-1">
      <div class="empty-icon">⚠️</div>
      <h3>加载失败</h3>
      <p>${escHtml(msg)}</p>
    </div>`;
}

function updateResultCount(count, tab) {
  const el = document.getElementById(`${tab}-count`);
  if (el) el.textContent = count;
  const header = document.getElementById('results-count');
  if (header) header.textContent = `${count} 个结果`;
}

/* ═══════════════ Utils ═════════════════ */
function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ═══════════════ Init ═══════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  // Tab buttons
  $$('.sm-tab').forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });

  // Sidebar category filters
  $$('.sm-sidebar-item[data-cat]').forEach(item => {
    item.addEventListener('click', () => {
      $$('.sm-sidebar-item[data-cat]').forEach(i => i.classList.remove('active'));
      item.classList.add('active');
      state.activeCategory = item.dataset.cat;
      loadCatalog(item.dataset.cat);
    });
  });

  // Sidebar nature filters
  $$('.sm-sidebar-item[data-nature]').forEach(item => {
    item.addEventListener('click', () => {
      $$('.sm-sidebar-item[data-nature]').forEach(i => i.classList.remove('active'));
      item.classList.add('active');
      state.activeNature = item.dataset.nature;
      loadCatalog();
    });
  });

  // Search
  const searchInput = document.getElementById('sm-search-input');
  if (searchInput) searchInput.addEventListener('input', onSearchInput);

  // Sort
  const sortSelect = document.getElementById('sort-select');
  if (sortSelect) sortSelect.addEventListener('change', () => {
    state.sortBy = sortSelect.value;
    state.filteredSkills = sortSkills(state.filteredSkills);
    renderSkillGrid('catalog-grid', state.filteredSkills);
  });

  // Drawer close
  document.getElementById('sm-drawer-overlay')?.addEventListener('click', closeDrawer);
  document.getElementById('drawer-close-btn')?.addEventListener('click', closeDrawer);

  // Edit modal close
  document.getElementById('skill-edit-overlay')?.addEventListener('click', closeEditModal);

  // Export pack button
  document.getElementById('export-pack-btn')?.addEventListener('click', exportSelectedPack);

  // Studio init
  initStudio();
  initDropZone();

  // Initial load
  switchTab('catalog');
});
