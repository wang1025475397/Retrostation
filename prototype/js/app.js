/* ============================================================
 * Retrostation UI 原型 — 状态机与渲染
 * 上屏：交互主控（列表 / 网格 / 封面轮播 三视图）
 * 下屏：媒体区（视频优先，缺则封面）+ 元数据 + 媒体源页签
 * ============================================================ */

const $  = (s, r = document) => r.querySelector(s);
const elTop = $('#screenTop');
const elBot = $('#screenBottom');

/* ---------------- 布局常量（640×480 基准） ---------------- */
const L = {
  rowH: 34, rowGap: 4,             // 列表行
  gridCols: 4, gridRows: 3,        // 网格
  cardW: 158, cardGap: 12,         // 平台轮播卡
  gCardH: 272, gCardRatio: 0.72, gCardGap: 14,   // 游戏封面轮播卡
  stripH: 118,                     // 单屏模式详情条
  statusH: 28, headH: 44, barH: 30,
};

const LAYOUTS = ['list', 'grid', 'carousel'];
const LAYOUT_CN = { list: '列表', grid: '网格', carousel: '轮播' };

/* ---------------- 状态 ---------------- */
const state = {
  view: 'platforms',      // 'platforms' | 'games'
  layout: 'list',         // 'list' | 'grid' | 'carousel'
  single: false,          // 单屏兼容模式
  bottomLive: true,       // 副屏实时联动
  bottomVideo: true,      // 副屏媒体区是否播放视频（无视频则显示封面）
  platIdx: 3,             // 默认停在 FC
  gameIdx: 0,
  listTop: 0,
  filter: 'all',          // all | covered | missing
  sort: 'name',
  favorites: new Set(),
  modal: null,            // null | 'menu' | 'confirm' | 'launching'
  menuIdx: 0,
  toast: null,
  videoPaused: false,
};

// 预置几个收藏，便于展示收藏视图
['FC/魂斗罗', 'SFC/超时空之钥', 'GBA/口袋妖怪 绿宝石', 'NDS/马力欧卡丁车DS',
 'NEOGEO/拳皇98', 'MD/索尼克'].forEach(id => {
  const g = GAMES.ALL.find(x => x.id === id);
  if (g) state.favorites.add(g.id);
});

const MENU_ITEMS = [
  { key: 'screen',  label: '显示模式',   get: () => state.single ? '单屏' : '双屏' },
  { key: 'layout',  label: '游戏视图',   get: () => LAYOUT_CN[state.layout] },
  { key: 'bvideo',  label: '副屏视频',   get: () => (state.single ? '单屏已停用' : (state.bottomVideo ? '开' : '关')) },
  { key: 'sort',    label: '游戏排序',   get: () => ({ name: '名称', play: '最多游玩', recent: '最近游玩' })[state.sort] },
  { key: 'filter',  label: '封面筛选',   get: () => ({ all: '全部', covered: '仅已刮削', missing: '仅缺封面' })[state.filter] },
  { key: 'bottom',  label: '副屏联动',   get: () => state.bottomLive ? '开启' : '关闭' },
  { key: 'lang',    label: '语言',       get: () => '简体中文' },
  { key: 'theme',   label: '主题',       get: () => '琥珀 / 深色' },
  { key: 'bright',  label: '背光',       get: () => '上 140 / 下 140' },
  { key: 'scrape',  label: '跳转刮削',   get: () => 'Tiny Scraper' },
  { key: 'about',   label: '关于',       get: () => 'v0.1.0' },
];

/* ============================================================
 * 工具
 * ============================================================ */
const curSystem = () => SYSTEMS[state.platIdx];

function getGameList() {
  let list = getGames(curSystem().key, state) || [];
  if (state.filter === 'covered') list = list.filter(g => g.hasCover);
  if (state.filter === 'missing') list = list.filter(g => !g.hasCover);
  if (state.sort === 'play')   list = [...list].sort((a, b) => b.playCount - a.playCount);
  if (state.sort === 'recent') list = [...list].sort((a, b) => a.lastPlayed - b.lastPlayed);
  return list;
}

/** 副屏媒体区是否播视频：单屏模式下强制关（与 DESIGN §11 一致） */
function playVideo() {
  return state.bottomVideo && !state.single;
}

/** 列表缩略图：优先 Logo，没有则回退封面 */
function thumbMediaHTML(g, w, h) {
  if (g.hasLogo) return logoHTML(g, w, h, Math.round(h * 0.44));
  return coverHTML(g.seed, g.hasCover, '', 11);
}

function coverHTML(seed, hasCover, ini, fontSize) {
  if (!hasCover) return '<div class="cover is-missing"><div class="ini">?</div></div>';
  const st = coverStyle(seed, 0, 0);
  const label = ini ? `<div class="ini" style="font-size:${fontSize || 15}px">${ini}</div>` : '';
  return `<div class="cover" style="background-image:${st.backgroundImage}">${label}</div>`;
}

/** Logo 图（程序化：宽幅横条 + 名称） */
function logoHTML(g, w, h, fontSize) {
  if (!g || !g.hasLogo) return '';
  return `<div class="logoimg" style="width:${w}px;height:${h}px">
            <span style="font-size:${fontSize || 18}px">${esc(g.name)}</span>
          </div>`;
}

function keycap(t, rect) {
  return `<span class="keycap${rect ? ' rect' : ''}">${t}</span>`;
}

function stars(n) {
  return `<span class="stars">${'★'.repeat(n)}<i>${'★'.repeat(5 - n)}</i></span>`;
}

function toast(msg) {
  state.toast = msg;
  render();
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { state.toast = null; render(); }, 2000);
}

/* ============================================================
 * 上屏渲染
 * ============================================================ */
function renderTop() {
  const parts = [renderStatus()];
  parts.push(state.view === 'platforms' ? renderPlatformView() : renderGameView());
  parts.push(renderButtonBar());
  if (state.single) parts.push(renderStrip());
  parts.push(renderOverlays());
  if (state.toast) parts.push(`<div class="toast">${state.toast}</div>`);
  elTop.innerHTML = parts.join('');
}

function renderStatus() {
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  return `
  <div class="statusbar">
    <span id="clock">${hh}:${mm}</span>
    <span class="badge">WiFi</span>
    <span class="badge">${state.single ? '单屏' : '双屏'}</span>
    <span class="spacer"></span>
    <span class="badge">TF1</span>
    <span class="badge">56℃</span>
    <span class="badge"><span class="batt"><i style="width:87%"></i></span>87%</span>
  </div>`;
}

function renderButtonBar() {
  const next = LAYOUT_CN[LAYOUTS[(LAYOUTS.indexOf(state.layout) + 1) % 3]];
  const b = state.view === 'platforms'
    ? [keycap('A') + '进入', keycap('Y') + '排序', keycap('START', true) + '菜单']
    : [keycap('A') + '开始', keycap('B') + '返回', keycap('Y') + '收藏',
       keycap('X') + next, keycap('SELECT', true) + '筛选', keycap('START', true) + '菜单'];
  return `<div class="buttonbar">${b.map(x => `<span class="btnhint">${x}</span>`).join('')}</div>`;
}

/* ---------------- 平台页 ---------------- */
function renderPlatformView() {
  const sys = curSystem();
  const total = SYSTEMS.length;

  const cards = SYSTEMS.map((s, i) => {
    const isAct = i === state.platIdx;
    const seed = hashStr(s.key);
    return `
    <div class="pcard${isAct ? ' is-active' : ''}" data-act="plat" data-i="${i}">
      <div class="pcard__art" style="background-image:${coverStyle(seed, 0, 0).backgroundImage}">
        <div class="big">${esc(initials(s.cn))}</div>
      </div>
      <div class="pcard__name">
        <b>${esc(s.cn)}</b>
        <span>${s.kind === 'special' ? esc(s.en) : `${fmt(s.roms)} 个`}</span>
      </div>
    </div>`;
  }).join('');

  const tx = 320 - L.cardW / 2 - state.platIdx * (L.cardW + L.cardGap);
  const preview = (getGames(sys.key, state) || []).slice(0, 6);
  const strip = preview.length
    ? `<div class="thumbstrip">
         <span class="lbl">预览</span>
         ${preview.map(g => `<div class="th">${coverHTML(g.seed, g.hasCover, initials(g.name), 13)}</div>`).join('')}
       </div>`
    : `<div class="thumbstrip"><span class="lbl" style="color:#4A4A50">该分类暂无内容</span></div>`;

  const coverRate = sys.roms ? Math.round((sys.covers / sys.roms) * 100) : 0;
  const chips = sys.kind === 'special'
    ? `<span class="chip acc">${esc(sys.desc)}</span>`
    : `<span class="chip">${fmt(sys.roms)} ROM</span>
       <span class="chip ${coverRate >= 80 ? 'ok' : 'warn'}">封面 ${coverRate}%</span>
       <span class="chip">${sys.kind === 'standalone' ? '独立模拟器' : 'RetroArch'}</span>`;

  return `
  <div class="pagehead">
    <span class="bar"></span>
    <h1>游戏库</h1>
    <span class="sub">${total} 个平台</span>
    <span class="right">${state.platIdx + 1} / ${total}</span>
  </div>
  <div class="content">
    <div class="carousel">
      <div class="carousel__track" style="transform:translateX(${tx}px)">${cards}</div>
    </div>
    <div class="platinfo">
      <span class="t">${esc(sys.cn)}</span>
      <span class="d">${esc(sys.en)}</span>
      <span class="chips">${chips}</span>
    </div>
    ${strip}
  </div>`;
}

/* ---------------- 游戏页（三视图） ---------------- */
function renderGameView() {
  const sys = curSystem();
  const list = getGameList();
  const total = list.length;

  if (total === 0) {
    return `
    <div class="pagehead">
      <span class="bar"></span><h1>${esc(sys.cn)}</h1>
      <span class="sub">${esc(sys.en)}</span>
      <span class="right">0 个</span>
    </div>
    <div class="content">
      <div class="empty">
        <div class="big">∅</div>
        <div class="t">${state.filter === 'missing' ? '没有缺失封面的游戏' : '这里还没有游戏'}</div>
        <div class="s">按 SELECT 切换筛选，或按 B 返回</div>
      </div>
    </div>`;
  }

  if (state.gameIdx >= total) state.gameIdx = total - 1;
  if (state.gameIdx < 0) state.gameIdx = 0;

  const contentH = 480 - L.statusH - L.headH - L.barH - (state.single ? L.stripH : 0);
  const body = state.layout === 'list'      ? renderList(list, contentH)
             : state.layout === 'grid'      ? renderGrid(list, contentH)
             :                                renderCarousel(list, contentH);

  const filterName = { all: '全部', covered: '已刮削', missing: '缺封面' }[state.filter];

  return `
  <div class="pagehead">
    <span class="bar"></span>
    <h1>${esc(sys.cn)}</h1>
    <span class="sub">${fmt(total)} 个</span>
    <span class="right">${filterName} · ${LAYOUT_CN[state.layout]}</span>
  </div>
  <div class="content">${body}</div>`;
}

/** ① 列表视图 */
function renderList(list, contentH) {
  const perPage = Math.max(1, Math.floor(contentH / (L.rowH + L.rowGap)));
  let top = state.listTop;
  if (state.gameIdx < top) top = state.gameIdx;
  if (state.gameIdx > top + perPage - 1) top = state.gameIdx - perPage + 1;
  top = Math.max(0, Math.min(top, Math.max(0, list.length - perPage)));
  state.listTop = top;

  const rows = list.slice(top, top + perPage).map((g, i) => {
    const idx = top + i;
    const sel = idx === state.gameIdx;
    const fav = state.favorites.has(g.id);
    return `
    <div class="list__row${sel ? ' is-sel' : ''}" data-act="game" data-i="${idx}"
         style="top:${8 + i * (L.rowH + L.rowGap)}px">
      <div class="thumb">${thumbMediaHTML(g, 80, 26)}</div>
      <div class="nm">${esc(g.name)}</div>
      ${fav ? '<span class="star">★</span>' : ''}
      <div class="idx">${idx + 1}/${list.length}</div>
    </div>`;
  }).join('');

  const thumbH = Math.max(24, contentH * perPage / list.length);
  const sbTop = (contentH - thumbH) * (list.length > 1 ? state.gameIdx / (list.length - 1) : 0);

  return `
  <div class="list">
    ${rows}
    <div class="scrollbar"><i style="top:${sbTop}px;height:${thumbH}px"></i></div>
  </div>`;
}

/** ② 网格视图 */
function renderGrid(list, contentH) {
  const rows = state.single ? 2 : L.gridRows;
  const cellH = Math.floor((contentH - 16 - 8 * (rows - 1)) / rows);
  const perPage = L.gridCols * rows;
  let top = Math.floor(state.gameIdx / perPage) * perPage;

  const cards = list.slice(top, top + perPage).map((g, i) => {
    const idx = top + i;
    const sel = idx === state.gameIdx;
    const fav = state.favorites.has(g.id);
    return `
    <div class="gcard${sel ? ' is-sel' : ''}" data-act="game" data-i="${idx}">
      <div class="gcard__art">${coverHTML(g.seed, g.hasCover, initials(g.name), 19)}
        ${fav ? '<span class="fav-dot">★</span>' : ''}
      </div>
      <div class="gcard__nm">${esc(g.name)}</div>
    </div>`;
  }).join('');

  return `<div class="grid" style="grid-template-rows:repeat(${rows},${cellH}px)">${cards}</div>`;
}

/* 轮播缩放/透明度梯度 */
const CF_SCALE = [1, 0.75, 0.62, 0.62];
const CF_OPACITY = [1, 0.42, 0.18, 0.10];

/** ③ 封面轮播视图（coverflow） */
function renderCarousel(list, contentH) {
  const cardH = Math.max(140, Math.min(L.gCardH, contentH - 70));
  const cardW = Math.round(cardH * L.gCardRatio);
  const top = 6;

  // 相邻卡片已缩放，位置必须按「缩放后的宽度」累积，否则间距会越走越大
  const wOf = k => cardW * CF_SCALE[k];
  const pos = [0];
  for (let k = 1; k <= 3; k++) pos[k] = pos[k - 1] + (wOf(k - 1) + wOf(k)) / 2 + L.gCardGap;

  const from = Math.max(0, state.gameIdx - 3);
  const to = Math.min(list.length - 1, state.gameIdx + 3);

  const cards = [];
  for (let i = from; i <= to; i++) {
    const g = list[i];
    const off = i - state.gameIdx;
    const a = Math.min(3, Math.abs(off));
    const scale = CF_SCALE[a];
    const op = CF_OPACITY[a];
    const cx = 320 + Math.sign(off) * pos[a];
    const x = cx - cardW / 2;
    const fav = state.favorites.has(g.id);
    cards.push(`
    <div class="ccard${off === 0 ? ' is-sel' : ''}" data-act="game" data-i="${i}"
         style="left:${x}px;top:${top}px;width:${cardW}px;height:${cardH}px;
                transform:scale(${scale});opacity:${op};z-index:${10 - a}">
      ${coverHTML(g.seed, g.hasCover, initials(g.name), Math.round(cardH * 0.16))}
      ${fav ? '<span class="fav-dot">★</span>' : ''}
      ${g.hasLogo
        ? `<div class="ccard__logo">${logoHTML(g, cardW - 20, 28, 13)}</div>`
        : `<div class="ccard__name">${esc(g.name)}</div>`}
    </div>`);
  }

  const g = list[state.gameIdx];
  const stripTop = top + cardH + 10;
  const stripH = contentH - stripTop - 6;
  const banner = g.hasLogo
    ? `<div class="cbanner">${logoHTML(g, 240, Math.min(56, stripH - 14), 21)}</div>`
    : `<div class="cbanner cbanner--text">
         <b>${esc(g.name)}</b>
         <span>${esc(g.systemCn)} · ${esc(g.publisher)} · ${g.year} · ${esc(g.genre)}</span>
       </div>`;

  return `
  <div class="coverflow">
    ${cards.join('')}
    <div class="cfname" style="top:${stripTop}px;height:${stripH}px">${banner}</div>
    <div class="cfcount">${state.gameIdx + 1} / ${list.length}</div>
  </div>`;
}

/* ---------------- 单屏模式：底部详情条 ---------------- */
function renderStrip() {
  if (state.view === 'platforms') {
    const sys = curSystem();
    const rate = sys.roms ? Math.round((sys.covers / sys.roms) * 100) : 0;
    return `
    <div class="strip">
      <div class="strip__art" style="background-image:${coverStyle(hashStr(sys.key), 0, 0).backgroundImage}">
        <div class="cover"><div class="ini" style="font-size:26px">${esc(initials(sys.cn))}</div></div>
      </div>
      <div class="strip__body">
        <h3>${esc(sys.cn)}　<span style="font-size:11px;color:var(--text-dim)">${esc(sys.en)}</span></h3>
        <div class="l">${esc(sys.desc)}</div>
        <div class="l">ROM ${fmt(sys.roms)} · 封面覆盖 ${rate}% · ${sys.kind === 'standalone' ? '独立模拟器' : 'RetroArch'}</div>
        <div class="l" style="color:var(--accent)">单屏模式：副屏内容已并入此区域</div>
      </div>
    </div>`;
  }
  const list = getGameList();
  const g = list[state.gameIdx];
  if (!g) return `<div class="strip"><div class="strip__body"><div class="l">无选中项</div></div></div>`;
  const fav = state.favorites.has(g.id);
  return `
  <div class="strip">
    <div class="strip__art">${coverHTML(g.seed, g.hasCover, initials(g.name), 22)}</div>
    <div class="strip__body">
      <h3>${fav ? '<span style="color:var(--accent)">★ </span>' : ''}${esc(g.name)}</h3>
      <div class="l">${esc(g.systemCn)} · ${esc(g.publisher)} · ${esc(g.release)} · ${esc(g.genre)}</div>
      <div class="l">${stars(g.rating)}　已玩 ${g.playCount} 次 · 上次 ${g.lastPlayed} 天前</div>
      <div class="l mdesc"><p>${esc(g.desc)}</p></div>
    </div>
  </div>`;
}

/* ---------------- 覆盖层 ---------------- */
function renderOverlays() {
  if (!state.modal) return '';
  if (state.modal === 'menu') {
    const items = MENU_ITEMS.map((m, i) => `
      <div class="menu__item${i === state.menuIdx ? ' is-sel' : ''}" data-act="menu" data-i="${i}">
        <span>${esc(m.label)}</span><span class="val">${esc(m.get())}</span>
      </div>`).join('');
    return `
    <div class="overlay">
      <div class="dialog">
        <div class="dialog__h">系统菜单</div>
        <div class="menu">${items}</div>
        <div class="dialog__f">
          <span class="btnhint">${keycap('A')}确认</span>
          <span class="btnhint">${keycap('B')}返回</span>
        </div>
      </div>
    </div>`;
  }
  if (state.modal === 'confirm') {
    return `
    <div class="overlay">
      <div class="dialog">
        <div class="dialog__h" style="color:var(--danger)">退出 Retrostation</div>
        <div class="dialog__b">将退出前端并返回系统菜单。gamelist.xml 会先落盘保存。</div>
        <div class="dialog__f">
          <span class="btn" data-act="dlg-cancel">取消</span>
          <span class="btn danger" data-act="dlg-exit">确认退出</span>
        </div>
      </div>
    </div>`;
  }
  if (state.modal === 'launching') {
    const g = state.launchGame;
    return `
    <div class="overlay launching">
      <div class="spinner"></div>
      <div class="t">正在启动《${esc(g ? g.name : '')}》</div>
      <div class="s">${esc(curSystem().core)}</div>
      <div class="s">已停视频解码 · 让出双屏 · 交还 SDL</div>
    </div>`;
  }
  return '';
}

/* ============================================================
 * 下屏渲染：媒体区（视频优先）+ 元数据
 * ============================================================ */
function renderBottom() {
  if (!state.bottomLive && state.modal !== 'launching') {
    elBot.innerHTML = `
    <div class="btitle"><span class="tag">IDLE</span>副屏联动已关闭</div>
    <div class="bbody"><div class="empty"><div class="big">⏸</div>
      <div class="t">副屏内容已冻结</div>
      <div class="s">在设置中重新开启「副屏联动」</div></div></div>
    <div class="bhint">下屏 DSI-2 · 640×480</div>`;
    return;
  }
  elBot.innerHTML = state.modal === 'launching' ? renderBottomLaunching()
                  : state.view === 'platforms' ? renderBottomPlatform()
                  : renderBottomGame();
}

function renderBottomLaunching() {
  const g = state.launchGame;
  return `
  <div class="btitle"><span class="tag">LAUNCH</span>让位中</div>
  <div class="bbody">
    <div class="bmedia">
      <div class="bmedia__view is-cover">
        ${coverHTML(g ? g.seed : 1, g ? g.hasCover : false, initials(g ? g.name : '?'), 34)}
      </div>
    </div>
    <div class="bmeta">
      <h2>${esc(g ? g.name : '')}</h2>
      <div class="sub">${esc(curSystem().cn)}</div>
      <div class="divider"></div>
      <dl class="kv">
        <dt>启动器</dt><dd>${esc(curSystem().kind === 'standalone' ? '独立模拟器' : 'RA_launch.sh')}</dd>
        <dt>核心</dt><dd>${esc(curSystem().core)}</dd>
        <dt>路径</dt><dd>/mnt/mmc/Roms/${esc(g ? g.system : '')}/</dd>
      </dl>
      <div class="divider"></div>
      <div style="font-size:12px;color:var(--text-dim)">
        游戏结束后由守护脚本重新拉起前端，<br>并按 resume 恢复当前位置。
      </div>
    </div>
  </div>
  <div class="bhint">${keycap('A')} 开始　${keycap('B')} 返回　${keycap('Y')} 收藏</div>`;
}

/** 媒体区：有视频播视频，否则显示封面（不展示媒体类型标记） */
function mediaBlock(g) {
  const m = resolveMedia(g, playVideo());
  const paused = state.videoPaused ? ' is-paused' : '';
  const isVid = m.kind === 'video';

  const inner = isVid
    ? `${coverHTML(g.seed, g.hasCover, '', 30)}<div class="videolayer${paused}"></div>`
    : coverHTML(g.seed, g.hasCover, initials(g.name), 34);

  return `
  <div class="bmedia__view is-${m.kind}">
    ${inner}
    ${isVid ? '<div class="vidprog"><i></i></div>' : ''}
  </div>`;
}

function renderBottomGame() {
  const list = getGameList();
  const g = list[state.gameIdx];
  if (!g) {
    return `
    <div class="btitle">游戏详情</div>
    <div class="bbody"><div class="empty"><div class="big">∅</div>
      <div class="t">没有可显示的游戏</div></div></div>
    <div class="bhint">下屏 DSI-2 · 640×480</div>`;
  }
  const fav = state.favorites.has(g.id);
  const sys = SYSTEMS.find(s => s.key === g.system) || curSystem();

  return `
  <div class="btitle">${fav ? '<span class="tag">★ 收藏</span>' : ''}游戏详情</div>
  <div class="bbody">
    <div class="bmedia">
      ${mediaBlock(g)}
      <div class="blogo">
        ${g.hasLogo ? logoHTML(g, 300, 44, 19)
                    : `<div class="blogo__text">${esc(g.name)}</div>`}
      </div>
    </div>
    <div class="bmeta">
      <div class="sub">${esc(g.systemCn)} · ${esc(g.publisher)}</div>
      <div class="mrating">${stars(g.rating)}<span class="num">${g.rating.toFixed(1)}</span></div>
      <div class="divider"></div>
      <dl class="kv">
        <dt>类型</dt><dd>${esc(g.genre)}</dd>
        <dt>人数</dt><dd>1-${g.players} 人</dd>
        <dt>发布</dt><dd>${esc(g.release)}</dd>
        <dt>核心</dt><dd>${esc(sys.core)}</dd>
      </dl>
      <div class="divider"></div>
      <div class="mdesc">
        <div class="k">简介</div>
        <p>${esc(g.desc)}</p>
      </div>
      <div class="divider"></div>
      <div class="statgrid">
        <div class="statcard acc"><div class="v">${g.playCount}</div><div class="k">已玩次数</div></div>
        <div class="statcard"><div class="v">${g.lastPlayed} 天</div><div class="k">上次游玩</div></div>
      </div>
      <div style="font-size:11px;color:#5C5C63;margin-top:auto">
        元数据来源：gamelist.xml（ES-DE）
      </div>
    </div>
  </div>
  <div class="bhint">
    ${keycap('A')} 开始　${keycap('B')} 返回　${keycap('Y')} ${fav ? '取消收藏' : '收藏'}　${keycap('X')} ${LAYOUT_CN[LAYOUTS[(LAYOUTS.indexOf(state.layout) + 1) % 3]]}
  </div>`;
}

function renderBottomPlatform() {
  const sys = curSystem();

  if (sys.kind === 'special') {
    const list = getGames(sys.key, state) || [];
    const gallery = list.length
      ? `<div class="collage">${list.slice(0, 6).map(g => `
          <div class="cell" style="background-image:${coverStyle(g.seed, 0, 0).backgroundImage}">
            <div class="ini">${esc(initials(g.name))}</div>
          </div>`).join('')}</div>`
      : `<div class="empty" style="position:relative;flex:1"><div class="big">∅</div>
           <div class="t">暂无内容</div>
           <div class="s">${sys.key === 'FAV' ? '在游戏列表按 Y 添加收藏' : '先去玩几个游戏吧'}</div></div>`;
    return `
    <div class="btitle"><span class="tag">${esc(sys.key)}</span>${esc(sys.cn)}</div>
    <div class="bbody">
      <div class="bart" style="flex:0 0 216px">
        <div class="cover" style="background-image:${coverStyle(hashStr(sys.key), 0, 0).backgroundImage}">
          <div class="ini" style="font-size:40px">${esc(initials(sys.cn))}</div>
        </div>
      </div>
      <div class="bcol">
        <div class="bmeta">
          <h2>${esc(sys.cn)}</h2>
          <div class="sub">${esc(sys.desc)}</div>
          <div class="divider"></div>
          <div class="statgrid">
            <div class="statcard acc"><div class="v">${fmt(list.length)}</div><div class="k">游戏总数</div></div>
            <div class="statcard ok"><div class="v">${fmt(list.filter(g => g.hasCover).length)}</div><div class="k">已有封面</div></div>
            <div class="statcard"><div class="v">${fmt(state.favorites.size)}</div><div class="k">收藏</div></div>
            <div class="statcard"><div class="v">${SYSTEMS.filter(s => s.kind !== 'special').length}</div><div class="k">平台</div></div>
          </div>
        </div>
        ${gallery}
      </div>
    </div>
    <div class="bhint">${keycap('A')} 进入　${keycap('←')}${keycap('→')} 切换分类　${keycap('START', true)} 菜单</div>`;
  }

  const rate = sys.roms ? Math.round((sys.covers / sys.roms) * 100) : 0;
  const favCount = (GAMES[sys.key] || []).filter(g => state.favorites.has(g.id)).length;
  return `
  <div class="btitle"><span class="tag">${esc(sys.key)}</span>平台详情</div>
  <div class="bbody">
    <div class="bart bart--plat">
      <div class="cover" style="background-image:${coverStyle(hashStr(sys.key), 0, 0).backgroundImage}">
        <div class="ini" style="font-size:44px">${esc(initials(sys.cn))}</div>
      </div>
    </div>
    <div class="bmeta">
      <h2>${esc(sys.cn)}</h2>
      <div class="sub">${esc(sys.en)}</div>
      <div class="divider"></div>
      <dl class="kv">
        <dt>目录</dt><dd>/mnt/mmc/Roms/${esc(sys.key)}/</dd>
        <dt>模拟器</dt><dd>${sys.kind === 'standalone' ? '独立模拟器' : 'RetroArch'}</dd>
        <dt>核心</dt><dd>${esc(sys.core)}</dd>
        <dt>媒体</dt><dd>Imgs/ · video/ · logo/</dd>
      </dl>
      <div class="divider"></div>
      <div class="statgrid">
        <div class="statcard acc"><div class="v">${fmt(sys.roms)}</div><div class="k">ROM 总数</div></div>
        <div class="statcard ${rate >= 80 ? 'ok' : ''}"><div class="v">${rate}%</div><div class="k">封面覆盖</div></div>
        <div class="statcard"><div class="v">${favCount}</div><div class="k">已收藏</div></div>
        <div class="statcard"><div class="v">${fmt(Math.max(0, sys.roms - sys.covers))}</div><div class="k">待刮削</div></div>
      </div>
      <div style="font-size:12px;color:var(--text-dim);margin-top:2px">${esc(sys.desc)}</div>
    </div>
  </div>
  <div class="bhint">${keycap('A')} 进入平台　${keycap('←')}${keycap('→')} 切换平台　${keycap('B')} 返回</div>`;
}

/* ============================================================
 * 副屏节流（模拟真机 90ms 策略）
 * ============================================================ */
let bottomTimer = null;
function scheduleBottom(immediate) {
  if (!state.bottomLive && state.modal !== 'launching') { renderBottom(); return; }
  if (immediate) { renderBottom(); return; }
  clearTimeout(bottomTimer);
  bottomTimer = setTimeout(renderBottom, 90);
}

function render(bottomImmediate) {
  renderTop();
  if (bottomImmediate) renderBottom();
  else scheduleBottom(false);
  renderSidePanel();
}

/* ============================================================
 * 交互
 * ============================================================ */
function move(delta) {
  if (state.modal === 'menu') {
    state.menuIdx = (state.menuIdx + delta + MENU_ITEMS.length) % MENU_ITEMS.length;
  } else if (state.view === 'platforms') {
    state.platIdx = (state.platIdx + delta + SYSTEMS.length) % SYSTEMS.length;
  } else {
    const list = getGameList();
    if (!list.length) return;
    const step = state.layout === 'grid' ? delta * L.gridCols : delta;
    state.gameIdx = Math.max(0, Math.min(list.length - 1, state.gameIdx + step));
  }
  render();
}

/** 左右方向键：平台页切平台；列表 ±10；网格 ±1；轮播 ±1 */
function lateral(delta) {
  if (state.modal) return;
  if (state.view === 'games') {
    const list = getGameList();
    if (!list.length) return;
    const step = state.layout === 'list' ? delta * 10 : delta;
    state.gameIdx = Math.max(0, Math.min(list.length - 1, state.gameIdx + step));
  } else {
    state.platIdx = (state.platIdx + delta + SYSTEMS.length) % SYSTEMS.length;
  }
  render();
}

/** L1 / R1 翻页 */
function page(delta) {
  if (state.view === 'games') {
    const list = getGameList();
    if (!list.length) return;
    const size = state.layout === 'grid'
      ? L.gridCols * (state.single ? 2 : L.gridRows)
      : 10;
    state.gameIdx = Math.max(0, Math.min(list.length - 1, state.gameIdx + delta * size));
  } else if (state.modal !== 'menu') {
    state.platIdx = (state.platIdx + delta + SYSTEMS.length) % SYSTEMS.length;
  }
  render();
}

function jump(edge) {
  const list = getGameList();
  if (state.view === 'games' && list.length) {
    state.gameIdx = edge === 'first' ? 0 : list.length - 1;
  } else if (state.view === 'platforms') {
    state.platIdx = edge === 'first' ? 0 : SYSTEMS.length - 1;
  }
  render();
}

function pressA() {
  if (state.modal === 'menu')     { applyMenu(); return; }
  if (state.modal)                { return; }

  if (state.view === 'platforms') {
    state.view = 'games';
    state.gameIdx = 0;
    state.listTop = 0;
    render(true);
  } else {
    launch();
  }
}

function pressB() {
  if (state.modal === 'menu' || state.modal === 'confirm') { state.modal = null; render(); return; }
  if (state.modal) return;
  if (state.view === 'games') { state.view = 'platforms'; render(true); }
}

function toggleFav() {
  if (state.modal || state.view !== 'games') return;
  const g = getGameList()[state.gameIdx];
  if (!g) return;
  if (state.favorites.has(g.id)) {
    state.favorites.delete(g.id);
    toast(`已取消收藏 <span class="acc">${esc(g.name)}</span>`);
  } else {
    state.favorites.add(g.id);
    toast(`已收藏 <span class="acc">${esc(g.name)}</span>`);
  }
  render(true);
}

/** X 键：列表 → 网格 → 轮播 */
function cycleLayout() {
  if (state.view !== 'games') return;
  state.layout = LAYOUTS[(LAYOUTS.indexOf(state.layout) + 1) % LAYOUTS.length];
  toast(`视图：<span class="acc">${LAYOUT_CN[state.layout]}</span>`);
  render();
}

function cycleFilter() {
  const order = ['all', 'covered', 'missing'];
  state.filter = order[(order.indexOf(state.filter) + 1) % order.length];
  state.gameIdx = 0;
  state.listTop = 0;
  const names = { all: '全部游戏', covered: '仅已刮削', missing: '仅缺封面' };
  toast(`筛选：<span class="acc">${names[state.filter]}</span>`);
  render(true);
}

/** 副屏视频开关（无视频时自动显示封面，无需手动切媒体类型） */
function toggleVideo() {
  state.bottomVideo = !state.bottomVideo;
  toast(`副屏视频：<span class="acc">${state.bottomVideo ? '开' : '关（显示封面）'}</span>`);
  render(true);
}

function launch() {
  const g = getGameList()[state.gameIdx];
  if (!g) return;
  state.launchGame = g;
  state.modal = 'launching';
  state.videoPaused = true;      // 让位前停视频（对应 ffmpeg terminate）
  render(true);

  setTimeout(() => {
    g.playCount += 1;
    g.lastPlayed = 0;
    state.modal = null;
    state.launchGame = null;
    state.videoPaused = false;
    toast(`已从 <span class="acc">${esc(g.name)}</span> 返回 · 现场已恢复 · gamelist 已写回`);
    render(true);
  }, 1600);
}

function applyMenu() {
  const m = MENU_ITEMS[state.menuIdx];
  switch (m.key) {
    case 'screen': state.single = !state.single; syncSwitches(); break;
    case 'layout': cycleLayout(); break;
    case 'bvideo': toggleVideo(); break;
    case 'bottom': state.bottomLive = !state.bottomLive; syncSwitches(); break;
    case 'sort': {
      const o = ['name', 'play', 'recent'];
      state.sort = o[(o.indexOf(state.sort) + 1) % o.length];
      break;
    }
    case 'filter': cycleFilter(); break;
    case 'scrape': toast('将退出并启动 <span class="acc">Tiny Scraper</span>'); break;
    default: toast(`${esc(m.label)}：<span class="acc">${esc(m.get())}</span>`); break;
  }
  state.modal = null;
  render(true);
}

/* ---------------- 键盘 ---------------- */
const KEYMAP = {
  ArrowUp:    () => move(-1),
  ArrowDown:  () => move(1),
  ArrowLeft:  () => lateral(-1),
  ArrowRight: () => lateral(1),
  PageUp:     () => page(-1),
  PageDown:   () => page(1),
  Home:       () => jump('first'),
  End:        () => jump('last'),
  Enter:      () => pressA(),
  Backspace:  () => pressB(),
  Escape:     () => { if (state.modal && state.modal !== 'launching') { state.modal = null; render(); } },
};

document.addEventListener('keydown', e => {
  const k = e.key;
  if (['Backspace', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown'].includes(k)) {
    e.preventDefault();
  }
  if (state.modal === 'launching') return;   // 启动中忽略一切输入
  if (KEYMAP[k]) { KEYMAP[k](); return; }

  switch (k.toLowerCase()) {
    case 'y': toggleFav(); break;
    case 'x': cycleLayout(); break;
    case 'v': toggleVideo(); break;
    case 's': cycleFilter(); break;
    case 'm': state.modal = state.modal === 'menu' ? null : 'menu'; state.menuIdx = 0; render(); break;
    case 'p': state.modal = state.modal === 'confirm' ? null : 'confirm'; render(); break;
  }
});

/* ---------------- 鼠标 / 触摸 ---------------- */
function bindScreen(node) {
  node.addEventListener('click', e => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const act = t.dataset.act;
    const i = parseInt(t.dataset.i, 10);

    if (act === 'plat') {
      if (state.modal) return;
      if (state.platIdx === i) pressA(); else { state.platIdx = i; render(); }
      return;
    }
    if (act === 'game') {
      if (state.modal) return;
      if (state.gameIdx === i) pressA(); else { state.gameIdx = i; render(); }
      return;
    }
    if (act === 'menu')  { state.menuIdx = i; render(); return; }
    if (act === 'dlg-cancel') { state.modal = null; render(); return; }
    if (act === 'dlg-exit') {
      state.modal = null;
      toast('已退出前端（原型中不会真的关闭）');
      return;
    }
  });
}
bindScreen(elTop);
bindScreen(elBot);

/* ---------------- 侧栏 ---------------- */
function syncSwitches() {
  $('#swSingle').classList.toggle('on', state.single);
  $('#swBottom').classList.toggle('on', state.bottomLive);
  $('#device').classList.toggle('is-single', state.single);
  $('#dotBottom').classList.toggle('off', state.single);
}
$('#swSingle').addEventListener('click', () => {
  state.single = !state.single;
  syncSwitches();
  toast(state.single
    ? '切换到 <span class="acc">单屏模式</span>：副屏内容并入主屏下分区（视频默认关）'
    : '切换到 <span class="acc">双屏模式</span>：上下屏分工');
  render(true);
});
$('#swBottom').addEventListener('click', () => {
  state.bottomLive = !state.bottomLive;
  syncSwitches();
  render(true);
});

function renderSidePanel() {
  const list = state.view === 'games' ? getGameList() : [];
  const g = list[state.gameIdx];
  const sys = curSystem();
  const m = g ? resolveMedia(g, playVideo()) : null;

  $('#stateBar').innerHTML =
`view      <b>${state.view}</b>
layout    <b>${state.layout}</b>  (${LAYOUT_CN[state.layout]})
screen    <b>${state.single ? 'single (1 output)' : 'dual (DSI-1 + DSI-2)'}</b>
platform  <b>${sys.key}</b>  (${sys.cn})
games     <b>${state.view === 'games' ? list.length : sys.roms}</b>
selected  <b>${g ? state.gameIdx + 1 : '-'}</b>${g ? '  ' + esc(g.name) : ''}
bottom    <b>${g ? m.kind : '-'}</b>   video <b>${playVideo() ? 'on' : 'off'}</b>
filter    <b>${state.filter}</b>   sort <b>${state.sort}</b>
favorites <b>${state.favorites.size}</b>
modal     <b>${state.modal || 'none'}</b>`;

  const xmlBox = $('#gamelistXml');
  if (!xmlBox) return;
  if (state.view === 'games' && g) {
    xmlBox.innerHTML =
`<span class="c">&lt;?xml version="1.0"?&gt;</span>
<span class="c">&lt;gameList&gt;</span>
${hlXml(gamelistEntry(g, state.favorites.has(g.id)))}
<span class="c">  ...（共 ${list.length} 条）</span>
<span class="c">&lt;/gameList&gt;</span>`
      .replace(/\n/g, '<br>');
  } else {
    xmlBox.innerHTML = `<span class="c">// 进入某个平台后，这里显示选中游戏
// 在 gamelist.xml 中的条目</span>`;
  }
}

/** XML 简易高亮（逐行处理，避免正则互相污染） */
function hlXml(s) {
  return s.split('\n').map(line => {
    // 注意：gamelistEntry() 内部已对值做过 XML 转义，这里不要再 esc 一次
    const m = line.match(/^(\s*)<(\w+)>(.*?)<\/\2>$/);
    if (!m) return esc(line);
    return `${m[1]}<span class="c">&lt;${m[2]}&gt;</span>`
         + `<span class="v">${m[3]}</span>`
         + `<span class="c">&lt;/${m[2]}&gt;</span>`;
  }).join('\n');
}

/* ---------------- 自适应缩放 ---------------- */
function fit() {
  const d = $('#device');
  d.style.marginBottom = '0px';
  const h = d.offsetHeight || 1024;
  const s = Math.min(1, (window.innerHeight - 56) / h);
  d.style.setProperty('--fit', s.toFixed(3));
  d.style.marginBottom = `${-(1 - s) * h}px`;
}
window.addEventListener('resize', fit);

/* ---------------- 启动 ---------------- */
syncSwitches();
render(true);
fit();
setInterval(() => {
  const c = $('#clock');
  if (!c) return;
  const n = new Date();
  c.textContent = `${String(n.getHours()).padStart(2, '0')}:${String(n.getMinutes()).padStart(2, '0')}`;
}, 15000);
