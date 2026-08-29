/* ============================================================
 * Retrostation UI 原型 — Mock 数据层
 * 数据规模参考真机实测：/mnt/mmc/Roms 下各平台 ROM 与 Imgs 数量
 * ============================================================ */

/** 平台定义：key=目录名, cn=中文名, en=英文名, roms/covers=实测数量级,
 *  core=RetroArch 核心或独立模拟器, kind=retro|standalone|special */
const SYSTEMS = [
  { key: 'ALL',     cn: '全部游戏', en: 'All Games',        roms: 0,   covers: 0,   core: '—',                              kind: 'special',     color: '#6C7A89', desc: '横跨所有平台的游戏总览' },
  { key: 'FAV',     cn: '收藏',     en: 'Favorites',        roms: 0,   covers: 0,   core: '—',                              kind: 'special',     color: '#E8A33D', desc: '你标记为喜欢的游戏' },
  { key: 'RECENT',  cn: '最近游玩', en: 'Recently Played',  roms: 0,   covers: 0,   core: '—',                              kind: 'special',     color: '#4CAF50', desc: '最近 30 个玩过的游戏' },
  { key: 'FC',      cn: '红白机',   en: 'NES / Famicom',    roms: 515, covers: 498, core: 'fceumm_libretro.so',             kind: 'retro',       color: '#D9534F', desc: '任天堂 8 位家用机，1983 年发售' },
  { key: 'SFC',     cn: '超级任天堂', en: 'Super Famicom',  roms: 410, covers: 380, core: 'snes9x2005_plus_libretro.so',    kind: 'retro',       color: '#8E7CC3', desc: '任天堂 16 位家用机，1990 年发售' },
  { key: 'GBA',     cn: 'GBA',      en: 'Game Boy Advance', roms: 302, covers: 265, core: 'mgba_libretro.so',               kind: 'retro',       color: '#3B78A7', desc: '任天堂掌上游戏机，2001 年发售' },
  { key: 'GBC',     cn: 'GB Color', en: 'Game Boy Color',   roms: 96,  covers: 80,  core: 'gambatte_libretro.so',           kind: 'retro',       color: '#7A9E5C', desc: '任天堂彩色掌机，1998 年发售' },
  { key: 'GB',      cn: 'Game Boy', en: 'Game Boy',         roms: 88,  covers: 70,  core: 'gambatte_libretro.so',           kind: 'retro',       color: '#6E7B63', desc: '任天堂初代掌机，1989 年发售' },
  { key: 'MD',      cn: '世嘉五代', en: 'Mega Drive',       roms: 210, covers: 180, core: 'picodrive_libretro.so',          kind: 'retro',       color: '#2C5F8A', desc: '世嘉 16 位家用机，1988 年发售' },
  { key: 'NDS',     cn: '任天堂DS', en: 'Nintendo DS',      roms: 102, covers: 96,  core: 'setNDS64.sh (drastic)',          kind: 'standalone',  color: '#5AA0C9', desc: '双屏掌机 · 原生上下屏输出' },
  { key: 'PS',      cn: 'PlayStation', en: 'PlayStation',   roms: 23,  covers: 20,  core: 'pcsx_rearmed_libretro.so',       kind: 'retro',       color: '#9AA0A6', desc: '索尼初代家用机，1994 年发售' },
  { key: 'PSP',     cn: 'PSP',      en: 'PlayStation Port.',roms: 12,  covers: 10,  core: 'ppsspp/run_gles.sh',             kind: 'standalone',  color: '#4A4F57', desc: '索尼掌机，2004 年发售' },
  { key: 'N64',     cn: '任天堂64', en: 'Nintendo 64',      roms: 45,  covers: 38,  core: 'mupen64plus_next_libretro.so',   kind: 'retro',       color: '#3E6B4F', desc: '任天堂 3D 家用机，1996 年发售' },
  { key: 'CPS1',    cn: 'CPS1',     en: 'Capcom CPS-1',     roms: 30,  covers: 28,  core: 'fbalpha2012_cps1_libretro.so',   kind: 'retro',       color: '#A0522D', desc: '卡普空街机基板，1988 年' },
  { key: 'CPS2',    cn: 'CPS2',     en: 'Capcom CPS-2',     roms: 34,  covers: 32,  core: 'fbalpha2012_cps2_libretro.so',   kind: 'retro',       color: '#B5651D', desc: '卡普空街机基板，1993 年' },
  { key: 'NEOGEO',  cn: 'Neo Geo',  en: 'Neo Geo',          roms: 120, covers: 110, core: 'fbalpha2012_neogeo_libretro.so', kind: 'retro',       color: '#C0392B', desc: 'SNK 街机基板，1990 年' },
  { key: 'FBNEO',   cn: 'FB Neo',   en: 'FinalBurn Neo',    roms: 260, covers: 240, core: 'fbneo_libretro.so',              kind: 'retro',       color: '#8E44AD', desc: '街机合集核心' },
  { key: 'MAME',    cn: 'MAME',     en: 'MAME 2003-Plus',   roms: 180, covers: 90,  core: 'mame2003_plus_libretro.so',      kind: 'retro',       color: '#7F8C8D', desc: '街机模拟器，覆盖面最广' },
  { key: 'PCE',     cn: 'PC Engine',en: 'PC Engine',        roms: 60,  covers: 50,  core: 'mednafen_pce_fast_libretro.so',  kind: 'retro',       color: '#D35400', desc: 'NEC 家用机，1987 年发售' },
  { key: 'SMS',     cn: '世嘉MS',   en: 'Master System',    roms: 70,  covers: 60,  core: 'smsplus_libretro.so',            kind: 'retro',       color: '#1F6F8B', desc: '世嘉 8 位家用机，1985 年' },
  { key: 'GG',      cn: 'Game Gear',en: 'Game Gear',        roms: 55,  covers: 48,  core: 'gearsystem_libretro.so',         kind: 'retro',       color: '#16A085', desc: '世嘉彩色掌机，1990 年' },
  { key: 'WS',      cn: 'WonderSwan',en:'WonderSwan',       roms: 25,  covers: 22,  core: 'mednafen_wswan_libretro.so',     kind: 'retro',       color: '#8D6E63', desc: '万代掌机，1999 年' },
  { key: 'DC',      cn: 'Dreamcast',en: 'Dreamcast',        roms: 30,  covers: 26,  core: 'flycast_libretro.so',            kind: 'retro',       color: '#D68910', desc: '世嘉末代家用机，1998 年' },
  { key: 'SATURN',  cn: '土星',     en: 'Sega Saturn',      roms: 28,  covers: 24,  core: 'saturn/launch.sh HLE',           kind: 'standalone',  color: '#5D6D7E', desc: '世嘉 32 位家用机，1994 年' },
  { key: 'PORTS',   cn: '移植游戏', en: 'Ports',            roms: 6,   covers: 6,   core: 'PortMaster',                     kind: 'standalone',  color: '#27AE60', desc: '原生 Linux 移植游戏' },
];

/** 各平台的模拟游戏名池（真机 FC 目录为中文名，此处取真实风格样本） */
const NAME_POOLS = {
  FC: ['超级马力欧兄弟', '魂斗罗', '坦克大战', '冒险岛', '赤色要塞', '沙罗曼蛇', '恶魔城', '忍者龙剑传',
       '双截龙', '热血高校', '炸弹人', '吃豆子', '松鼠大作战', '绿色兵团', '忍者蛙', '洛克人',
       '银河战士', '塞尔达传说', '最终幻想', '勇者斗恶龙', '吞食天地', '重装机兵', '圣火徽章', '蝙蝠侠'],
  SFC: ['超级马力欧世界', '塞尔达传说 众神的三角力量', '超时空之钥', '最终幻想VI', '超级银河战士', '恶魔城X',
        '街头霸王II', '魂斗罗精神', '大金刚国度', '星际火狐', '洛克人X', '圣剑传说', '火焰纹章',
        '皇家骑士团', '天地创造', '幻想传说', '超级恶魔城IV', '快打旋风', '超级马里奥赛车', '地球冒险'],
  GBA: ['口袋妖怪 绿宝石', '塞尔达传说 缩小帽', '恶魔城 晓月圆舞曲', '火焰纹章 烈火之剑', '黄金太阳',
        '马力欧赛车 超级巡回赛', '洛克人Zero', '高级战争', '逆转裁判', '瓦力欧制造', '光明之魂',
        '索尼克 Advance', '星之卡比 梦之泉', '机器人大战OG', '最终幻想战略版', '合金弹头'],
  GBC: ['口袋妖怪 金银', '塞尔达传说 大地之章', '俄罗斯方块', '瓦力欧大陆', '洛克人世界', '恶魔城 漆黑前奏曲',
        '萨尔达传说 时空之章', '索尼克 Pocket', '马里奥高尔夫', '游戏王'],
  GB: ['俄罗斯方块', '超级马力欧大陆', '口袋妖怪 红', '塞尔达传说 织梦岛', '打砖块', '银河战士II',
       '洛克人世界', '大金刚', '越野摩托', '青蛙过河'],
  MD: ['索尼克', '梦幻之星IV', '光明与黑暗', '怒之铁拳II', '魂斗罗 铁血兵团', '银河之星', '幽游白书',
       '侍魂', '多人坦克', '兽王记', '大航海时代II', '卡通世界', '蚯蚓战士', '洛克人 威力战士'],
  NDS: ['任天狗狗', '马力欧卡丁车DS', 'New超级马力欧兄弟', '口袋妖怪 钻石', '动物森友会', '塞尔达传说 幻影沙漏',
        '逆转裁判', '恶魔城 苍月的十字架', '节奏天国', '应援团', '雷顿教授', '勇者斗恶龙IX'],
  PS: ['最终幻想VII', '生化危机2', '合金装备', '恶魔城 月下夜想曲', '铁拳3', '寄生前夜', '放浪冒险谭',
       '女神异闻录', 'GT赛车', '恐龙危机'],
  PSP: ['怪物猎人P3', '战神 斯巴达之魂', '最终幻想 核心危机', '合金装备 和平行者', '啪嗒嘭', '梦幻之星 携带版2'],
  N64: ['塞尔达传说 时之笛', '马力欧64', '马里奥赛车64', '星际火狐64', '007黄金眼', '任天堂明星大乱斗',
        '塞尔达传说 姆吉拉的假面', '纸片马力欧', 'F-Zero X', '生化危机2'],
  NEOGEO: ['拳皇98', '合金弹头3', '饿狼传说', '龙虎之拳', '侍魂 天草降临', '月华之剑士', '风云默示录',
           '越南大战', '战国传承', '痛快进行曲'],
  CPS1: ['快打旋风', '名将', '三国志II', '圆桌骑士', '恐龙快打', '吞食天地', '街头霸王', '惩罚者'],
  CPS2: ['街头霸王ZERO3', '恶魔战士', 'X战警对街霸', '漫威对卡普空', '装甲勇士', '超级街霸II', '异形对铁血战士'],
  FBNEO: ['合金弹头X', '铁拳', '双截龙', '出击飞龙', '雷鸟IV', '机动战士', '落日骑士', '暴力克星',
          '魔法气泡', '圣铠传说'],
  MAME: ['吃豆人', '大金刚', '小蜜蜂', '打砖块', '雪人兄弟', '雷电', '街霸', '空手道', '三维弹球', '俄罗斯方块'],
  PCE: ['恶魔城X 血之轮回', '雷电', 'PC原人', '银河战士', '沙罗曼蛇', '热血高校', '妖怪道中记', '源平讨魔传'],
  SMS: ['亚历克西斯', '刺猬索尼克', '幻想地带', '忍者', '公路追击', '梦幻之星', '大金刚', '炸弹人'],
  GG: ['索尼克', '俄罗斯方块', '忍者龙剑传', '漫画地带', 'GG忍者', '眼镜蛇', '超级马里奥', '出击飞龙'],
  WS: ['恶魔城 漆黑前奏曲', '最终幻想', '超级机器人大战', '逆转裁判', '铁拳', '洛克人', '疯狂出租车'],
  DC: ['索尼克大冒险', '莎木', '生化危机 代号维罗妮卡', '疯狂出租车', ' VR战士3', '罪恶工具X', '梦幻之星在线'],
  SATURN: ['索尼克R', '守护英雄', '光明力量III', '恶灵古堡', '铁拳', '龙之力量', '街头霸王ZERO3', '大航海时代'],
  PORTS: ['Celeste', 'SuperTuxKart', 'Cave Story', 'OpenLara', 'Quake', 'SMW Retro Remix'],
};

const GENRES = ['动作', '角色扮演', '平台跳跃', '射击', '格斗', '策略', '竞速', '冒险', '解谜', '体育', '模拟', '音乐'];
const PUBLISHERS = ['任天堂', '卡普空', '科乐美', '世嘉', '史克威尔', '艾尼克斯', 'SNK', '万代', '索尼', '光荣', 'Technos', 'Atlus'];
const DESCS = [
  '经典名作，手感扎实，至今仍值得一玩。',
  '画面与音乐在当时属顶级水准，关卡设计精巧。',
  '难度偏高，但通关后的成就感无与伦比。',
  '支持双人同乐，是当年客厅娱乐的首选。',
  '隐藏要素丰富，多次重玩仍能发现新内容。',
  '系列承上启下之作，奠定了后续作品的基调。',
];

/** 稳定哈希：用于生成确定性的封面配色 */
function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

/** 生成一个游戏对象 */
function makeGame(system, i) {
  const pool = NAME_POOLS[system.key] || ['未知游戏'];
  const base = pool[i % pool.length];
  const round = Math.floor(i / pool.length);
  const name = round === 0 ? base : `${base} ${round + 1}`;
  const seed = hashStr(system.key + name);
  const hasCover = (seed % 100) < Math.round((system.covers / Math.max(system.roms, 1)) * 100);
  // 视频与 Logo 覆盖率：真实情况下视频最难刮，Logo 次之
  const hasVideo = hasCover && ((seed >> 3) % 100) < 58;
  const hasLogo  = ((seed >> 6) % 100) < 62;
  const year = 1983 + ((seed >> 5) % 32);
  const month = 1 + ((seed >> 13) % 12);
  const day = 1 + ((seed >> 17) % 28);
  const release = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  return {
    id: `${system.key}/${name}`,
    name,
    system: system.key,
    systemCn: system.cn,
    genre: GENRES[seed % GENRES.length],
    publisher: PUBLISHERS[(seed >> 3) % PUBLISHERS.length],
    year,
    release,
    players: 1 + ((seed >> 8) % 4),
    desc: DESCS[(seed >> 10) % DESCS.length],
    rating: 1 + ((seed >> 7) % 5),
    playCount: (seed >> 9) % 60,
    lastPlayed: (seed >> 11) % 40,
    hasCover, hasVideo, hasLogo,
    seed,
  };
}

/** 生成全部平台（含 3 个虚拟聚合平台）的游戏数据 */
const GAMES = {};
SYSTEMS.forEach(sys => {
  if (sys.kind === 'special') return;
  const list = [];
  for (let i = 0; i < sys.roms; i++) list.push(makeGame(sys, i));
  list.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'));
  GAMES[sys.key] = list;
});

// 聚合平台
GAMES.ALL = SYSTEMS.filter(s => s.kind !== 'special')
  .flatMap(s => GAMES[s.key])
  .sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'));
GAMES.RECENT = [...GAMES.ALL].sort((a, b) => a.lastPlayed - b.lastPlayed).slice(0, 30);
GAMES.FAV = [];

// 补上聚合平台的统计
SYSTEMS[0].roms = GAMES.ALL.length;
SYSTEMS[0].covers = GAMES.ALL.filter(g => g.hasCover).length;

/** 取某平台的游戏列表（FAV 单独处理） */
function getGames(key, state) {
  if (key === 'FAV') return GAMES.ALL.filter(g => state.favorites.has(g.id));
  return GAMES[key] || [];
}

/** 由 seed 生成封面样式（程序化占位图，代替真实 PNG） */
function coverStyle(seed, w, h) {
  const hueA = seed % 360;
  const hueB = (hueA + 40 + (seed >> 4) % 120) % 360;
  const motif = seed % 4;
  const layers = {
    0: `radial-gradient(circle at 70% 28%, hsla(${hueB},72%,62%,.95) 0 26%, transparent 26.5%)`,
    1: `conic-gradient(from ${hueA % 360}deg at 62% 34%, hsla(${hueB},75%,60%,.95) 0 25%, transparent 25%)`,
    2: `linear-gradient(115deg, hsla(${hueB},78%,58%,.95) 0 30%, transparent 30.5%)`,
    3: `radial-gradient(ellipse at 34% 74%, hsla(${hueB},70%,60%,.95) 0 34%, transparent 34.5%)`,
  }[motif];
  return {
    backgroundImage: `${layers}, linear-gradient(160deg, hsl(${hueA},46%,26%) 0%, hsl(${hueA},52%,14%) 100%)`,
  };
}

/** 取游戏名首字（用于占位图上的大字） */
function initials(name) {
  const cleaned = name.replace(/[^\u4e00-\u9fa5A-Za-z0-9]/g, '');
  return (cleaned.slice(0, 2) || '?');
}

/** 数字千分位 */
function fmt(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/* ============================================================
 * 媒体解析：封面 / 视频 / Logo
 * 目录约定（与 docs/DESIGN.md §6.3 一致）：
 *   <SYS>/Imgs/<name>.png   封面   gamelist: <cover>（兼容 <image>/<thumbnail>）
 *   <SYS>/video/<name>.mp4  视频   gamelist: <video>
 *   <SYS>/logo/<name>.png   Logo   gamelist: <marquee>（兼容 <wheel>）
 * ============================================================ */

/** 解析当前应展示的媒体：视频优先，缺则封面，再缺 Logo（无手动切换入口） */
function resolveMedia(g, playVideo) {
  if (!g) return { kind: 'none' };
  if (playVideo && g.hasVideo) return { kind: 'video' };
  if (g.hasCover) return { kind: 'cover' };
  if (g.hasLogo)  return { kind: 'logo' };
  return { kind: 'none' };
}

/** 生成 ES-DE 兼容的 gamelist.xml 单条 <game> */
function gamelistEntry(g, favorite) {
  if (!g) return '';
  const ext = { FC: 'nes', SFC: 'sfc', GBA: 'gba', GB: 'gb', GBC: 'gbc', MD: 'md', NDS: 'nds',
                PS: 'cue', PSP: 'iso', N64: 'n64', NEOGEO: 'zip', CPS1: 'zip', CPS2: 'zip',
                FBNEO: 'zip', MAME: 'zip', PCE: 'pce', SMS: 'sms', GG: 'gg', WS: 'ws',
                DC: 'chd', SATURN: 'cue', PORTS: 'sh' }[g.system] || 'zip';

  const pad = n => String(n).padStart(2, '0');
  const d = new Date(Date.now() - g.lastPlayed * 86400000);
  const lastplayed = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;

  const L = [];
  L.push('  <game>');
  L.push(`    <path>./${esc(g.name)}.${ext}</path>`);
  L.push(`    <name>${esc(g.name)}</name>`);
  L.push(`    <desc>${esc(g.desc)}</desc>`);
  L.push(`    <rating>${(g.rating / 5).toFixed(2)}</rating>`);
  L.push(`    <releasedate>${g.year}0101T000000</releasedate>`);
  L.push(`    <developer>${esc(g.publisher)}</developer>`);
  L.push(`    <publisher>${esc(g.publisher)}</publisher>`);
  L.push(`    <genre>${esc(g.genre)}</genre>`);
  L.push(`    <players>1-${g.players}</players>`);
  L.push(`    <playcount>${g.playCount}</playcount>`);
  L.push(`    <lastplayed>${lastplayed}</lastplayed>`);
  L.push(`    <favorite>${favorite ? 'true' : 'false'}</favorite>`);
  if (g.hasCover) L.push(`    <cover>./Imgs/${esc(g.name)}.png</cover>`);
  if (g.hasVideo) L.push(`    <video>./video/${esc(g.name)}.mp4</video>`);
  if (g.hasLogo)  L.push(`    <marquee>./logo/${esc(g.name)}.png</marquee>`);
  L.push('  </game>');
  return L.join('\n');
}

/** 转义 XML 特殊字符 */
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
