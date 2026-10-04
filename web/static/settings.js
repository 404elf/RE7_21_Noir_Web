const $ = (id) => document.getElementById(id);
const clone = (value) => structuredClone(value);
const categories = { attack: '进攻', guard: '防御', draw: '抽牌', control: '干扰', resource: '资源', target: '目标' };
const numberNames = ['Two', 'Three', 'Four', 'Five', 'Six', 'Seven'];
const clockPresets = [['1+0','子弹牌'],['2+1','子弹牌'],['3+0','超快牌'],['3+2','超快牌'],['5+0','超快牌'],['5+3','超快牌'],['10+0','快牌'],['10+5','快牌'],['15+10','快牌'],['30+0','慢牌'],['off','不限时'],['custom','自定义']];
function node(tag, text, className) {
  const value = document.createElement(tag);
  if (text !== undefined) value.textContent = text;
  if (className) value.className = className;
  return value;
}

export function settingsSummary(value) {
  const game = value.config.game_settings, timer = value.timer;
  const duration = (v, unit) => v === null ? '不限时' : `${v}${unit}`;
  const clock = !timer.enabled ? '不限时' : timer.mode === 'turn' ? `每次行动 ${duration(timer.turn_seconds, '秒')}`
    : timer.mode === 'round' ? `每人每局 ${duration(timer.round_seconds, '秒')}`
      : `整场 ${duration(timer.initial_minutes, '分钟')}，每次加 ${timer.increment_seconds} 秒`;
  return `目标 ${game.target_score} · 生命 ${game.max_hp} · 数字牌 ${game.deck_range_start}–${game.deck_range_end} · ${clock}`;
}

export class SettingsEditor {
  constructor(root) {
    this.root = root;
    this.dialog = $('settings-dialog');
    this.form = $('settings-form');
    this.locks = new Set(); this.weightPage = 0; this.gamePage = 0;
    window.addEventListener('resize', () => { if (this.catalog) { this.filterWeights(false); this.fitGame(); } });
    $('settings-roll').onclick = () => this.randomize(false);
    $('settings-roll-weights').onclick = () => this.randomize(true);
    $('settings-unlock').onclick = () => {
      this.locks.clear();
      for (const button of this.form.querySelectorAll('.setting-lock')) button.setAttribute('aria-pressed', 'false');
      this.status('已解除全部随机锁定。');
    };
    $('settings-ai').onclick = () => this.exportAI();
    $('weight-prev').onclick = () => { this.weightPage--; this.filterWeights(false); };
    $('weight-next').onclick = () => { this.weightPage++; this.filterWeights(false); };
    $('game-setting-prev').onclick = () => { this.gamePage--; this.fitGame(); };
    $('game-setting-next').onclick = () => { this.gamePage++; this.fitGame(); };
    $('timer-presets-back').onclick = () => { this.timerView = 'presets'; this.renderClockPresets(); };
    for (const button of this.form.querySelectorAll('[data-settings-tab]')) button.onclick = () => this.selectTab(button.dataset.settingsTab);
    $('settings-close').onclick = () => this.dialog.close();
    this.dialog.addEventListener('cancel', (event) => { if (this.busy) event.preventDefault(); });
    this.form.onsubmit = async (event) => {
      event.preventDefault();
      await this.perform(async () => {
        const value = await this.validate(this.read());
        await this.save(value);
        this.dialog.close();
      });
    };
    $('settings-preset').onchange = () => {
      const choice = $('settings-preset').value;
      if (choice === 'custom') return;
      try {
        const timer = this.readTimer();
        const config = choice === 'server' ? this.options.defaults.config : this.options.presets[Number(choice)].config;
        this.value = { config: clone(config), timer };
        this.render();
        $('settings-preset').value = choice;
        this.status('预设已载入，可继续修改。');
      } catch (error) { this.status(error.message, true); }
    };
    this.form.addEventListener('input', (event) => {
      if (event.target.id === 'settings-preset') return;
      $('settings-preset').value = 'custom';
      this.timerVisibility();
      if (event.target.closest('#timer-custom')) { this.customClock = true; this.renderClockPresets(); }
      this.status('');
    });
    $('settings-weight-search').oninput = () => this.filterWeights();
    $('settings-import').onclick = () => $('settings-file').click();
    $('settings-file').onchange = () => this.importFile();
    for (const kind of ['config', 'timer']) $(`settings-export-${kind}`).onclick = () => this.perform(async () => {
      const value = await this.validate(this.read());
      const url = URL.createObjectURL(new Blob([JSON.stringify(value[kind], null, 2) + '\n'], { type: 'application/json' }));
      const link = node('a'); link.href = url; link.download = `${kind}.json`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      this.status(`已导出 ${kind}.json，可用于原版或分享给朋友。`);
    });
  }

  async init(catalog) {
    this.catalog = catalog;
    const response = await fetch(new URL('api/settings', this.root));
    if (!response.ok) throw new Error('房间设置加载失败，请刷新后重试。');
    this.options = await response.json();
    return clone(this.options.defaults);
  }

  open(value, { readonly = false, save = null, room = false, tab = 'game' } = {}) {
    if (!this.options) throw new Error('房间设置尚未加载，请稍后重试。');
    this.value = clone(value);
    this.readonly = readonly; this.save = save;
    this.dialog.classList.toggle('settings-readonly', readonly);
    for (const id of ['settings-roll','settings-roll-weights','settings-unlock']) $(id).hidden = readonly;
    $('settings-title').textContent = readonly ? '本房间的完整规则' : room ? '修改房间规则' : '打造你的牌局';
    $('settings-intro').textContent = readonly ? '双方使用同一套规则。开局后锁定，再战也会沿用。'
      : room ? '保存后，双方准备状态会取消。请一起确认新规则后再开局。'
        : '从熟悉的预设开始，或写下自己的玩法。只影响你创建的房间。';
    $('settings-apply').textContent = room ? '保存房间规则' : '用于新房间';
    $('settings-apply').hidden = readonly;
    $('settings-import').hidden = $('settings-preset-label').hidden = readonly;
    this.customClock = false; this.timerView = 'presets'; this.gamePage = 0;
    this.render(); this.selectTab(tab); this.status(''); this.dialog.showModal();
    this.filterWeights(false); this.fitGame();
  }

  selectTab(tab) {
    this.dialog.dataset.activeTab = tab;
    this.activeTab = tab;
    $('settings-preset-label').hidden = this.readonly || tab === 'timer';
    for (const button of this.form.querySelectorAll('[data-settings-tab]')) {
      button.setAttribute('aria-pressed', String(button.dataset.settingsTab === tab));
    }
    for (const panel of this.form.querySelectorAll('[data-settings-panel]')) panel.hidden = panel.dataset.settingsPanel !== tab;
    this.form.querySelector('.settings-panels').scrollTop = 0;
    if (tab === 'weights') this.filterWeights(false);
    if (tab === 'game') this.fitGame();
  }

  lockButton(group, key) {
    const id = `${group}:${key}`, button = node('button', '锁', 'setting-lock text-button');
    button.type = 'button'; button.title = '随机生成时保留此项，仍可手动修改';
    button.setAttribute('aria-label', `随机时锁定${key}`);
    button.setAttribute('aria-pressed', String(this.locks.has(id))); button.hidden = this.readonly;
    button.onclick = () => {
      if (this.locks.has(id)) this.locks.delete(id); else this.locks.add(id);
      button.setAttribute('aria-pressed', String(this.locks.has(id)));
    };
    return button;
  }

  async randomize(weightsOnly) {
    await this.perform(async () => {
      const value = this.read(), choose = (values) => values[Math.floor(Math.random() * values.length)];
      if (!weightsOnly) {
        const changes = { max_hp: choose([5,10,15,20]), initial_trumps_count: choose([2,3,4,5]),
          round_reward_trumps_count: choose([1,2,3]), max_trumps_hand_size: choose([10,15,20]),
          max_active_trumps_on_table: 10, hit_draw_trump_probability: Math.round((.15 + Math.random() * .4) * 100) / 100,
          number_card_draw_probability: Math.round((.1 + Math.random() * .25) * 100) / 100 };
        for (const [key, number] of Object.entries(changes)) if (!this.locks.has(`game:${key}`)) value.config.game_settings[key] = number;
      }
      for (const key of Object.keys(value.config.trump_weights)) if (!this.locks.has(`weights:${key}`)) value.config.trump_weights[key] = choose([0,2,4,6,8,10]);
      this.value = await this.validate(value); this.render();
      $('settings-preset').value = 'custom';
      this.status(`已生成随机${weightsOnly ? '权重' : '玩法'}草稿，保留 ${this.locks.size} 个锁定项；保存后生效。`);
    });
  }

  async exportAI() {
    await this.perform(async () => {
      const value = await this.validate(this.read());
      const response = await fetch(new URL('AI-CONFIG-GUIDE.md', this.root));
      if (!response.ok) throw new Error('AI 配置说明加载失败。');
      let guide = await response.text();
      guide = guide.replace('玩家：复制本文件全文到聊天 AI', '玩家：将本文件上传或复制给聊天 AI');
      const beginning = guide.indexOf('拿到结果后，'), ending = guide.indexOf('\n\n---', beginning);
      if (beginning >= 0 && ending >= 0) guide = guide.slice(0, beginning) + '拿到结果后，保存为 UTF-8 的玩法 JSON，在网页版「游戏配置 → 导入配置 JSON」载入，检查后应用。此文件末尾的模板来自当前设置。' + guide.slice(ending);
      guide = guide.replace(/```json[\s\S]*?```/, '```json\n' + JSON.stringify(value.config, null, 2) + '\n```');
      const url = URL.createObjectURL(new Blob([guide], {type:'text/markdown;charset=utf-8'}));
      const link = node('a'); link.href = url; link.download = 'NOIR-AI配置参考.md'; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      this.status('AI 参考文件已生成并请求下载。把它和玩法需求交给聊天 AI，返回的 JSON 可直接导入。');
    });
  }

  renderClockPresets() {
    const timer = this.readTimer();
    const match = !timer.enabled ? 'off' : timer.mode === 'fischer'
      ? `${timer.initial_minutes}+${timer.increment_seconds}` : 'custom';
    const choice = this.customClock || !clockPresets.some(([id]) => id === match) ? 'custom' : match;
    $('timer-presets').replaceChildren(...clockPresets.map(([id, label]) => {
      const button = node('button', undefined, 'secondary'); button.type = 'button'; button.dataset.clock = id;
      button.append(node('strong', id.includes('+') ? id : label));
      if (id.includes('+')) button.append(node('small', label));
      button.setAttribute('aria-pressed', String(choice === id));
      button.disabled = this.readonly && id !== 'custom';
      button.onclick = () => {
        if (!this.readonly) this.customClock = id === 'custom';
        this.timerView = id === 'custom' ? 'custom' : 'presets';
        if (id !== 'custom') {
          this.value = this.read();
          if (id === 'off') this.value.timer.enabled = false;
          else {
            const [minutes, increment] = id.split('+').map(Number);
            Object.assign(this.value.timer, { enabled: true, mode: 'fischer', initial_minutes: minutes, increment_seconds: increment, preparation_seconds: 30 });
          }
          this.render();
        } else this.renderClockPresets();
      };
      return button;
    }));
    this.dialog.dataset.timerView = this.timerView;
    $('timer-custom').hidden = this.timerView !== 'custom';
    $('timer-preset-view').hidden = this.timerView === 'custom';
    $('timer-preset-summary').textContent = choice === 'off' ? '当前：不限时' : choice === 'custom' ? '自定义计时与亮牌等待'
      : `当前：${choice} · 整场 ${timer.initial_minutes} 分钟，抽牌或停牌后加 ${timer.increment_seconds} 秒。首局准备 30 秒，结算暂停。`;
  }

  refreshRoom(value) {
    if (!this.dialog.open || !this.readonly || JSON.stringify(value) === JSON.stringify(this.value)) return;
    this.value = clone(value); this.render();
    this.status('房主已更新规则，请重新查看后准备。');
  }

  numeric(row, value, id, readonly = this.readonly) {
    const input = node('input');
    input.type = 'number'; input.id = id;
    input.min = row.min; input.max = row.max; input.step = row.integer ? '1' : 'any';
    input.value = value ?? row.min; input.required = true; input.disabled = readonly;
    return input;
  }

  render() {
    const select = $('settings-preset');
    select.replaceChildren(new Option('服务器默认', 'server'),
      ...this.options.presets.map((p, i) => new Option(p.name, String(i))), new Option('自定义 / 已导入', 'custom'));
    const same = (config) => JSON.stringify(config) === JSON.stringify(this.value.config);
    const preset = this.options.presets.findIndex((p) => same(p.config));
    select.value = same(this.options.defaults.config) ? 'server' : preset >= 0 ? String(preset) : 'custom';
    $('settings-game').replaceChildren(...this.options.game_fields.map((row) => {
      const label = node('label', undefined, 'setting-field');
      label.append(node('span', `${row.label}：`, 'setting-name'), this.numeric(row, this.value.config.game_settings[row.key], `setting-${row.key}`),
        this.lockButton('game',row.key));
      label.title = `${row.min}–${row.max}${row.integer ? '，整数' : ''}`;
      return label;
    }));
    const enabled = $('timer-enabled'); enabled.checked = this.value.timer.enabled; enabled.disabled = this.readonly;
    const mode = $('timer-mode'); mode.value = this.value.timer.mode; mode.disabled = this.readonly;
    $('settings-timer').replaceChildren(...this.options.timer_fields.map((row) => {
      const field = node('div', undefined, 'setting-field timer-field'); field.dataset.key = row.key;
      const label = node('label', row.label); label.htmlFor = `timer-${row.key}`;
      const input = this.numeric(row, this.value.timer[row.key], `timer-${row.key}`);
      field.append(label, input);
      if (row.nullable) {
        const checkLabel = node('label', undefined, 'setting-check');
        const check = node('input'); check.type = 'checkbox'; check.id = `timer-${row.key}-unlimited`;
        check.checked = this.value.timer[row.key] === null; check.disabled = this.readonly;
        input.disabled = this.readonly || check.checked;
        check.onchange = () => { input.disabled = this.readonly || check.checked; };
        checkLabel.append(check, node('span', '不限时')); field.append(checkLabel);
      }
      field.title = `${row.min}–${row.max}`; return field;
    }));
    const cardWeight = (card) => this.value.config.trump_weights[card.name === 'ADD2+' ? 'Return+' : card.name];
    $('settings-weights').replaceChildren(...Object.values(this.catalog).filter((c) => !numberNames.includes(c.name))
      .sort((a,b) => (cardWeight(a) <= 0) - (cardWeight(b) <= 0)).map((card) => {
      const key = card.name === 'ADD2+' ? 'Return+' : card.name;
      const weight = this.value.config.trump_weights[key];
      const field = node('div', undefined, 'weight-row'); field.dataset.search = `${card.name} ${card.title} ${card.description}`.toLowerCase();
      const label = node('label', undefined, 'weight-name'); label.htmlFor = `weight-${key}`;
      label.append(node('strong', card.title), node('small', `${card.english} · ${categories[card.category]}`));
      label.title = card.description;
      const input = this.numeric({ min: 0, max: 10000 }, weight, `weight-${key}`);
      input.setAttribute('aria-label', `${card.title}权重`);
      const checkLabel = node('label', undefined, 'setting-check');
      const check = node('input'); check.type = 'checkbox'; check.checked = weight > 0; check.disabled = this.readonly;
      check.setAttribute('aria-label', `启用${card.title}`);
      let previous = weight || 1;
      check.onchange = () => { if (input.valueAsNumber > 0) previous = input.valueAsNumber; input.value = check.checked ? previous : 0; };
      input.oninput = () => { check.checked = input.valueAsNumber > 0; };
      checkLabel.append(check, node('span', '启用'));
      field.append(label, checkLabel, input, this.lockButton('weights', key)); return field;
    }));
    $('settings-weight-search').value = '';
    this.weightPage = 0;
    this.filterWeights();
    this.timerVisibility();
    this.renderClockPresets();
  }

  timerVisibility() {
    const mode = $('timer-mode').value;
    for (const field of $('settings-timer').children) {
      const key = field.dataset.key;
      field.hidden = ((key === 'turn_seconds' && mode !== 'turn')
        || (key === 'round_seconds' && mode !== 'round') || (['initial_minutes', 'increment_seconds'].includes(key) && mode !== 'fischer'));
    }
    $('settings-clock-note').textContent = $('timer-enabled').checked
      ? '行动 / 每局超时自动停牌；整场时间或首次准备超时判负。出牌与弃牌不加秒。'
      : '当前关闭行动计时；亮牌等待仍然生效。';
  }

  filterWeights(reset = true) {
    const query = $('settings-weight-search').value.trim().toLowerCase();
    const rows = [...$('settings-weights').children], matches = rows.filter((field) => field.dataset.search.includes(query));
    if (reset) this.weightPage = 0;
    const columns = window.matchMedia('(max-width:600px)').matches ? 1 : 2;
    const panelHeight = this.form.querySelector('.settings-panels').clientHeight;
    const rowHeight = rows.find((field) => !field.hidden)?.getBoundingClientRect().height || 38;
    const toolbarHeight = this.form.querySelector('.weight-toolbar').getBoundingClientRect().height || 34;
    const pagerHeight = this.form.querySelector('.weight-pages').getBoundingClientRect().height || 34;
    const perPage = columns * Math.max(1, Math.floor((panelHeight - toolbarHeight - pagerHeight - 28) / (rowHeight + 4)));
    const pages = Math.max(1, Math.ceil(matches.length / perPage));
    this.weightPage = Math.min(Math.max(this.weightPage, 0), pages - 1);
    const visible = new Set(matches.slice(this.weightPage * perPage, (this.weightPage + 1) * perPage));
    for (const field of rows) field.hidden = !visible.has(field);
    $('weight-prev').disabled = this.weightPage === 0; $('weight-next').disabled = this.weightPage === pages - 1;
    $('weight-page').textContent = `${this.weightPage + 1} / ${pages} · 共 ${matches.length} 项`;
  }

  fitGame() {
    if (!this.dialog.open || this.activeTab !== 'game') return;
    const rows = [...$('settings-game').children];
    const columns = window.matchMedia('(max-width:600px)').matches ? 1 : 2;
    const available = this.form.querySelector('.settings-panels').clientHeight;
    const rowHeight = Math.max(36, ...rows.filter((row) => !row.hidden).map((row) => row.getBoundingClientRect().height));
    const gap = parseFloat(getComputedStyle($('settings-game')).rowGap) || 0;
    const fullHeight = Math.ceil(rows.length / columns) * (rowHeight + gap) - gap;
    const perPage = fullHeight <= available ? rows.length
      : columns * Math.max(1, Math.floor((available - 40 + gap) / (rowHeight + gap)));
    const pages = Math.max(1, Math.ceil(rows.length / perPage));
    this.gamePage = Math.max(0, Math.min(this.gamePage, pages - 1));
    rows.forEach((row, index) => { row.hidden = index < this.gamePage * perPage || index >= (this.gamePage + 1) * perPage; });
    $('game-setting-pages').hidden = pages === 1;
    $('game-setting-page').textContent = `${this.gamePage + 1} / ${pages}`;
    $('game-setting-prev').disabled = this.gamePage === 0; $('game-setting-next').disabled = this.gamePage === pages - 1;
  }

  number(id) {
    const input = $(id);
    if (!Number.isFinite(input.valueAsNumber)) throw new Error('请填写完整的有限数字，不能留空。');
    return input.valueAsNumber;
  }

  readTimer() {
    const timer = { enabled: $('timer-enabled').checked, mode: $('timer-mode').value };
    for (const row of this.options.timer_fields) timer[row.key] = row.nullable && $(`timer-${row.key}-unlimited`).checked
      ? null : this.number(`timer-${row.key}`);
    return timer;
  }

  read() {
    const value = clone(this.value);
    for (const row of this.options.game_fields) value.config.game_settings[row.key] = this.number(`setting-${row.key}`);
    for (const card of Object.values(this.catalog)) {
      if (numberNames.includes(card.name)) continue;
      const key = card.name === 'ADD2+' ? 'Return+' : card.name;
      value.config.trump_weights[key] = this.number(`weight-${key}`);
    }
    value.timer = this.readTimer(); return value;
  }

  async validate(value) {
    const response = await fetch(new URL('api/settings/validate', this.root), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '配置无效。');
    return data;
  }

  async importFile() {
    const input = $('settings-file'), file = input.files[0]; input.value = '';
    if (!file) return;
    await this.perform(async () => {
      if (file.size > 16384) throw new Error('配置文件不能超过 16 KiB。');
      const data = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
      if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error('配置文件需要是 JSON 对象。');
      const current = this.read();
      const value = 'game_settings' in data || 'trump_weights' in data ? { config: data, timer: current.timer }
        : 'enabled' in data || 'mode' in data ? { config: current.config, timer: data }
          : 'config' in data && 'timer' in data ? data : null;
      if (!value) throw new Error('请选择原版 config.json、预设 JSON 或 timer.json。');
      this.value = await this.validate(value);
      this.render(); $('settings-preset').value = 'custom';
      this.status(`已载入 ${file.name}；应用前可以继续编辑。`);
    });
  }

  status(message, error = false) {
    $('settings-status').textContent = message;
    $('settings-status').classList.toggle('error', error);
    if (this.activeTab === 'weights') this.filterWeights(false);
    if (this.activeTab === 'game') this.fitGame();
  }

  async perform(operation) {
    if (this.busy) return;
    this.busy = true;
    const controls = [...this.dialog.querySelectorAll('button, input, select')].map((control) => [control, control.disabled]);
    controls.forEach(([control]) => { control.disabled = true; });
    try { await operation(); }
    catch (error) { this.status(error instanceof SyntaxError ? 'JSON 格式错误，请检查文件内容。' : error.message, true); }
    finally {
      this.busy = false; controls.forEach(([control, disabled]) => { control.disabled = disabled; });
      if (this.catalog) { this.filterWeights(false); this.fitGame(); }
    }
  }
}
