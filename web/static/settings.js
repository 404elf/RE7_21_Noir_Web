const $ = (id) => document.getElementById(id);
const clone = (value) => structuredClone(value);
const categories = { attack: '进攻', guard: '防御', draw: '抽牌', control: '干扰', resource: '资源', target: '目标' };
const numberNames = ['Two', 'Three', 'Four', 'Five', 'Six', 'Seven'];
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

  open(value, { readonly = false, save = null, room = false } = {}) {
    if (!this.options) throw new Error('房间设置尚未加载，请稍后重试。');
    this.value = clone(value);
    this.readonly = readonly; this.save = save;
    $('settings-title').textContent = readonly ? '本房间的完整规则' : room ? '修改房间规则' : '打造你的牌局';
    $('settings-intro').textContent = readonly ? '双方使用同一套规则。开局后锁定，再战也会沿用。'
      : room ? '保存后，双方准备状态会取消。请一起确认新规则后再开局。'
        : '从熟悉的预设开始，或写下自己的玩法。只影响你创建的房间。';
    $('settings-apply').textContent = room ? '保存房间规则' : '用于新房间';
    $('settings-apply').hidden = readonly;
    $('settings-import').hidden = $('settings-preset-label').hidden = readonly;
    this.render(); this.status(''); this.dialog.showModal();
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
      const label = node('label', row.label, 'setting-field');
      label.append(this.numeric(row, this.value.config.game_settings[row.key], `setting-${row.key}`),
        node('small', `${row.min}–${row.max}${row.integer ? '，整数' : ''}`));
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
      field.append(node('small', `${row.min}–${row.max}`)); return field;
    }));
    $('settings-weights').replaceChildren(...Object.values(this.catalog).filter((c) => !numberNames.includes(c.name)).map((card) => {
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
      field.append(label, checkLabel, input); return field;
    }));
    $('settings-weight-search').value = '';
    this.timerVisibility();
  }

  timerVisibility() {
    const mode = $('timer-mode').value;
    for (const field of $('settings-timer').children) {
      const key = field.dataset.key;
      field.hidden = !this.readonly && ((key === 'turn_seconds' && mode !== 'turn')
        || (key === 'round_seconds' && mode !== 'round') || (['initial_minutes', 'increment_seconds'].includes(key) && mode !== 'fischer'));
    }
    $('settings-clock-note').textContent = $('timer-enabled').checked
      ? '行动 / 每局超时自动停牌；整场时间或首次准备超时判负。出牌与弃牌不加秒。'
      : '当前关闭行动计时；亮牌等待仍然生效。';
  }

  filterWeights() {
    const query = $('settings-weight-search').value.trim().toLowerCase();
    for (const field of $('settings-weights').children) field.hidden = !field.dataset.search.includes(query);
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
  }

  async perform(operation) {
    if (this.busy) return;
    this.busy = true;
    const controls = [...this.dialog.querySelectorAll('button, input, select')].map((control) => [control, control.disabled]);
    controls.forEach(([control]) => { control.disabled = true; });
    try { await operation(); }
    catch (error) { this.status(error instanceof SyntaxError ? 'JSON 格式错误，请检查文件内容。' : error.message, true); }
    finally { this.busy = false; controls.forEach(([control, disabled]) => { control.disabled = disabled; }); }
  }
}
