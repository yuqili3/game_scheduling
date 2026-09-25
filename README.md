# 羽毛球赛事管理系统(抽签 / 排程 / 直播)

最初为《建群三周年活动:2026"我们来打羽毛球"大乱斗》(2026-07-19, Seattle Badminton Club)
开发:64 人(48 男 + 16 女)、8 队、三轮排位赛,决出第 1–8 名。现已把**赛前抽签通用化**,
同一套代码按 `data/<env>/` 目录承载多个赛事:

| env | 赛事 | 人数 / 队伍结构 | 状态 |
|-----|------|----------------|------|
| `prod` | 2026 大乱斗(7/19) | 64 人,8 队 × (1 男队长 + 5 男 + 2 女) | 已完赛,数据冻结 |
| `sim` | 大乱斗彩排沙盒 | 同 prod | 随便折腾 |
| `xd2026` | **2026 趣味混双友谊赛**(10/17, Redmond Badminton Club) | 24 人,6 队 × (1 指定男队长 + 1 男 + 2 女),混双搭档随抽签确定 | 赛前抽签可用;比赛日排程/App 待做 |

需求与架构文档按版本分文件、不原地覆盖:[spec.md](spec.md) 是首届(v3,冻结),
[spec_20260925.md](spec_20260925.md) 是当前版(指定队长固定席位 + 混双搭档抽签),上一版是
[spec_20260923.md](spec_20260923.md)(通用抽签 + xd2026)。以后每次需求变更另存 `spec_YYYYMMDD.md`。

## 核心理念:事件溯源,一切可复现

唯一事实来源是只追加的事件日志 `data/events.jsonl`。任何状态(队伍名单、比分、排名、
排程表)都是"把事件从头重放"的纯函数结果:

```
状态 = replay(config.yaml, events.jsonl)
```

- 所有随机操作(抽签、重抽、盲抽)的**种子记录在事件里**,同种子重跑结果必然一致
- 种子可以是整数,也可以是任意文本(比如现场喊出的一句话)。纯数字会按整数处理,
  所以 `20260925` 和 `"20260925"` 是同一个种子;`007` 会记为 `7`。字符串种子跨机器同样可复现
- 录入错误不改历史,追加更正事件即可,审计轨迹完整
- `teams_*.csv` 等快照文件只是重放结果的导出物,删掉可全部重新生成

## 目录结构

```
spec.md                  # 首届需求(v3,冻结);spec_YYYYMMDD.md = 之后各版完整快照
data/<env>/              # 每个赛事一个自包含目录: prod / sim / xd2026
  config.yaml            # 全部超参数(英文键名,中文注释): 人员(含 team_composition)/赛制/场地/时长/可视化/志愿者
  players*.csv           # 初始人员表(习惯分 players_female.csv / players_male.csv;仓库内为 celebrity 占位姓名)
                         #   必填列 id,name,gender;可选列 is_captain(缺省 false)、level(水平档,留空=不分档)
  events.jsonl           # 事件日志(唯一事实来源)
  tournament.db          # SQLite 并发写入层(不入 git,可由 jsonl 重建)
  teams_YYYYMMDD*.csv    # 分队快照(每次抽签/退赛/递补后导出,带日期戳)
core/                    # 纯函数层(不碰文件/时钟/全局随机数)
  models.py              # Player / Slot / TeamComposition / Event / TournamentState
  draw.py                # 通用抽签(队伍结构由 config 驱动)、退赛重抽、直接递补、盲抽(全部 (state, seed) -> result)
  rules.py               # 对阵树、晋级逻辑、比分判定、最终排名
  matchday.py            # 比赛日状态: 名单/盲抽/比分/缺人顶替的事件重放
  scheduler.py           # 时长模型 + 贪心排程 + 事件驱动重排
  replay.py              # 赛前事件重放
  io_utils.py            # IO 层: 配置/人员表/事件日志/CSV 导出
  eventstore.py          # SQLite 事件存储(志愿者并发写),自动导出回 events.jsonl
cli/                     # 命令行工具(写事件的唯一入口之一),赛前四件套均支持 --env / --dry-run
  draw_teams.py          # 初始抽签(种子驱动)
  import_teams.py        # 导入线下已抽好的名单
  withdraw.py            # 退赛重抽(大乱斗规则: 其余各队各出 1 人 + 候补重洗)
  substitute.py          # 直接递补(候补顶原位,无随机;趣味混双规则)
  schedule.py / rehearse.py  # 比赛日排程查看 / 彩排驱动器(仅首届赛制)
app/                     # Streamlit 直播 App
  Home.py                # 观众页: 场地五色面板 + 对阵树 + 排名 + 下一场
  pages/1_Score_Entry.py # 志愿者录入页(独立 PIN,只见分管场地,比分整轮留存)
  pages/2_Admin.py       # 管理页: 对阵抽签/名单/盲抽/缺席顶替 + 比分更正
  pages/3_Captain.py     # 队长页: 名单提交 + 种子盲抽 + 缺席/顶替(每队独立 PIN)
tests/                   # 单元测试: python3 -m unittest discover -s tests
```

## 环境分离(sim / prod)

数据按环境隔离在 `data/<env>/` 下,每个环境自带 config、名单、事件日志与数据库:

- **prod**:正式赛事数据。CLI 与 App 的默认环境
- **sim**:彩排与模拟沙盒,随便折腾,不碰正式数据

选择方式:环境变量 `GS_ENV=sim`,或 CLI 的 `--env sim` 参数。App 在非 prod
环境时每个页面顶部都会显示 🧪 SIMULATION 警示条,防止混淆。彩排驱动器
`cli/rehearse.py` 默认跑在 sim,并且拒绝在 prod 运行(除非 `--force-prod`)。

复用同一代码办新赛事 = 建一个新的环境目录(自带人员表与赛制 config),第一个例子是
`data/xd2026/`(2026 趣味混双友谊赛)。抽签相关的一切都从 config 的
`players.team_composition` 读取,**换一个男女配比只改 config,不改代码**:

```yaml
players:
  num_teams: 6
  team_composition:            # 每队结构 = 若干槽位,按顺序发牌
    - {gender: M, count: 1, role: captain}   # 队长槽放第一位,队长恒在每队首位
    - {gender: M, count: 1}    # role 缺省 member
    - {gender: F, count: 2}
  balance_by_level: true       # 人员表有 level 列时按水平分层抽,各队每档人数差 ≤1
  assign_pairs: true           # 抽完队伍后为每队随机配混双组合;标签 = 队伍字母 + 对号(A1/A2、B1/B2 …),队长的组合 = 第 1 对
```

每个槽位对应一个候选池,池大小必须恰好等于 `count × num_teams`,否则抽签直接报错。
人员表里填了 `fixed_team` 的人(比如主办方指定的队长)在抽签前就钉在那个队,不进候选池,
剩余名额按各队缺几个逐层轮转发牌。
xd2026 环境目前只支持赛前 CLI(抽签/导入/递补),App 与排程器尚未适配该赛制,不要在
该环境下启动 `streamlit`。

### 彩排(比赛日预演)

```bash
GS_ENV=sim streamlit run app/Home.py     # 终端 1: 起 app(页面带 SIMULATION 警示)
python3 cli/rehearse.py --interval 4     # 终端 2: 每 4 秒喂入一个事件,~12 分钟走完全程
```

驱动器按顺序执行:种子对阵抽签 → 各队名单 + 种子盲抽 → 逐局比分(含场地号与
分管志愿者署名)→ 三轮打满出排名。整场彩排由 `--seed` 决定,可完整复现。
浏览器开 `localhost:8501` 观看;期间可用志愿者/队长/Admin 页手动操作,与驱动器
事件并发交织,正好检验并发写入。

## 安装

```bash
pip3 install -r requirements.txt   # 赛前抽签 CLI 只需 pyyaml;streamlit 仅比赛日 App/彩排用
```

## 按赛事流程使用

### 阶段 0:配置参数

编辑 `config.yaml`。所有可调参数都在这里,程序不硬编码任何数值:

- **人员**: 队伍数、每队结构(`team_composition` 槽位列表)、是否按水平分层(`balance_by_level`)
- **赛制**: 轮数、每轮 5 场、5 场 3 胜、21 分制、缺人队伍改 15 分制、盲抽局类型
- **时长模型**: 女双 10 分钟/局,男双混双 13 分钟/局;强强对话每局 +3 分钟
  (判定阈值: 每局净胜 ≥6 或每场净胜 ≥12);连打两场之间休息 5 分钟
- **可视化**: 场地状态五色(绿=空闲/黄=热身/浅红·深红·紫红=第 1/2/3 局)
- **广播**: 每名志愿者的姓名、PIN、分管场地

### 阶段 1:报名结束,建立初始人员表

> 隐私说明:仓库内的所有人员表使用 **celebrity 占位姓名**,性别与队长结构和
> 真实名单一一对应;真实姓名只在线下保存,比赛日本地部署时替换即可。

`data/<env>/players_female.csv` 与 `players_male.csv`(程序读取该目录下所有 `players*.csv`)。
必填列 `id,name,gender`;可选列 `is_captain`(首届只固定 8 名男队长为 true,无队长赛事可省略此列)、
`level`(水平档位,如 A/B/C,留空即不分档)和 `fixed_team`(填队号 = 抽签前钉在该队,其余留空)。
队伍归属由下一步产生。

### 阶段 2:抽签分队(6/12)

两种方式,二选一:

```bash
# 方式 A: 程序抽签(种子记入事件日志,可复现);先 --dry-run 预览,确认后去掉再跑一次正式写入
python3 cli/draw_teams.py --seed 20260612 --dry-run
python3 cli/draw_teams.py --seed 20260612
python3 cli/draw_teams.py --seed "周五晚上见"        # 种子也可以是任意文本

# 方式 B: 线下已抽好,导入公布的名单(读根目录 players.csv)
python3 cli/import_teams.py --date 20260612
```

两者都会追加事件、按 config 的 `team_composition` 校验每队结构并导出
`data/<env>/teams_20260612.csv`。抽签算法(与队伍结构无关):

1. 每个槽位一个候选池(按性别 + 角色过滤),先排序再用 `random.Random(seed)` 洗牌,与文件行序无关
2. 有 `level` 列且 `balance_by_level: true` 时,池内先按档位分层、每档单独洗牌再拼接
3. 队伍顺序洗牌一次,然后轮转发牌(第 k 张给第 k mod n 队),同档人员因此均匀撒到各队
4. `initial_draw` 事件的 payload 记下当时的 `num_teams`、`team_composition` 和人员表指纹(id/性别/角色/档位的哈希,不含姓名),重放时与 config 及当前 CSV 比对;抽签后改 config 或补填 `level` 会报错而不是悄悄换一套名单,把占位名换成真名则不受影响

### 阶段 3:退赛重抽(6/12 – 7/18,可发生多次)

```bash
# 普通队员退赛: 候补姓名手工录入,种子由用户指定
python3 cli/withdraw.py --withdrawn 33 --substitute "候补姓名" --seed 777

# 队长退赛: 需额外指定本队一名男队员接任
python3 cli/withdraw.py --withdrawn 17 --new-captain 25 --substitute "候补姓名" --seed 778
```

自动执行大乱斗 PDF 规则:其余各队各随机抽 1 名同性别非队长成员,连同候补一起重新洗牌
分入各队空缺。输出带日期戳的新快照(同日多次自动编号 `_2`/`_3`)和逐步变更日志。
**用相同种子重跑必得相同结果。**

### 阶段 3b:直接递补(公布后不接受退赛的赛事)

```bash
# 候补者直接顶替退赛者的位置,不重抽、无随机
python3 cli/substitute.py --env xd2026 --withdrawn 7 --substitute "候补姓名"
```

追加 `substitute_direct` 事件(不带种子);候补者拿新 id,继承退赛者的性别与档位。

### 第二届:2026 趣味混双友谊赛(env `xd2026`)赛前操作

| 日期 | 动作 | 命令 |
|------|------|------|
| 9/23 报名开放 | 真实报名名单已填进 `data/xd2026/players_female.csv`(id 1–12 = 女生报名顺序)/ `players_male.csv`(id 13–24 = 男生报名顺序 1–12);6 名指定队长 `is_captain=true` 且 `fixed_team` = 队号(1 DreamWu、2 Michael Chou、3 Zack Chen、4 Yingtong Chen、5 Zhihao Hu、6 Guoquan Feng);`level` 列留空 | 编辑 CSV |
| 9/25 中午截止 | 预览抽签 | `python3 cli/draw_teams.py --env xd2026 --seed 20260925 --dry-run` |
| 9/25 抽签公布 | 正式抽签并导出快照 `data/xd2026/teams_20260925.csv`(含 `pair` 列: 组合标签,1 队 A1/A2、2 队 B1/B2 … 6 队 F1/F2,队长在第 1 对) | `python3 cli/draw_teams.py --env xd2026 --seed 20260925` |
| 9/25 之后 | 极端情况按候补名单递补 | `python3 cli/substitute.py --env xd2026 --withdrawn <id> --substitute "<姓名>"` |
| 10/17 比赛日 | 循环赛排程 / App 尚未适配(见 spec_20260923.md 4.2) | — |

种子可以任选:整数(比如抽签当天日期 + 现场报数)或任意文本(比如群里大家投票选出的一句话,
`--seed "我们来打羽毛球"`);种子记入 `data/xd2026/events.jsonl`,任何人拿同一份名单 + 同一种子
重跑都得到同样分队。

### 阶段 4:比赛日排程(7/19)

比赛日的一切动态都以事件追加进日志,排程器随事件实时重排:

| 事件类型 | 何时写入 | 关键字段 |
|---------|---------|---------|
| group_draw | Admin 页种子随机分组 | groups(G 编号 → 队伍);seed 入日志可复现 |
| lineup_submit | 每轮开赛前 | node、team、WD/MD1-3 名单 |
| blind_draw_result | 队长页种子盲抽(或 Admin 兜底) | node、team、被抽中队员;**seed 必带**,同种子可复现 |
| match_started | 主持人宣读并确认选手上场 | node、slot、court;确认后志愿者方可录分,排程将该场钉在此场地 |
| game_finished | 每局打完(志愿者录入) | node、slot、game、score、court;**双方人员未齐(名单/盲抽缺失)时拒绝录入** |
| absence_registered | 有人伤/缺 | team、absent_id(该队全部改 15 分制) |
| substitute_assigned | 每轮队长指定 | team、round、substitute_id(三轮不得重复,程序校验) |
| game_corrected | Admin 更正错录比分 | node、slot、game、score;补偿事件,原录入保留;更正后多余局自动剔除 |

命名约定:败者半区一律称 **consolation**(节点 `R2-CU`/`R2-CL`),不使用 loser。

查看当前排程与赛况:

```bash
python3 cli/schedule.py          # 场地×时间计划表 + 对阵树状态 + 实时排名
```

排程规则(全部由 config 驱动):

- **模板对齐**(参照赛程 PDF 附录的场地分配表,非朴素贪心):每场对抗内女双与
  男双 1–3 的前两局并行;**盲抽局在这 4 场各打完前两局并休息 ≥5 分钟后即可开打,
  不等可能的第三局**;条件第三局像模板的 "3rd, TBD" 一样推迟填入空闲场地。
  最坏情况完赛 23:32 → 22:23,利用率 58.4% → 71.3%
- **按对阵粒度提前开打**:下一轮某场对抗的两支队伍都打完上一轮即可上场,不等全轮
- 时长预估:基础时长 × 15 分制缩放(缺人队伍)+ 强强对话加时(双方组合都"强"
  才触发,第一轮不加时);未打的第三局按满时长预留(最坏情况),实际 2:0 后重排释放
- 10 块场地分两个半区各 5 块,组内贪心分配,目标最大化利用率

### 阶段 5:比赛日直播

```bash
pip3 install -r requirements.txt
streamlit run app/Home.py        # 场馆笔记本上启动,手机浏览器访问局域网 IP:8501
```

三个页面:

- **Home(观众页)**:10 块场地网格,颜色即状态(绿=空闲/黄=热身/浅红·深红·紫红=
  第 1/2/3 局),每格显示对阵、场上选手、实时比分与预计时间;对阵树三轮晋级图;
  实时排名;盲抽公示;"下一场"列表。按 `broadcast.refresh_seconds` 自动刷新
- **Score Entry(志愿者录入)**:从 `broadcast.volunteers` 选择姓名 + 独立 PIN 登录,
  只显示自己分管的场地;卡片展示对阵队伍、场上 4 名选手、下一局局号、当前分制,
  与场上人员核对后录入比分。**页面顶部有与观众页相同的十场地五色示意图,
  便于确认场上的人是对的**。写入前先过状态机校验,非法比分直接拒绝。
  **每提交一局,比分保留在页面并出现下一局的空输入框;打完的 match 留在场地
  区域直到本轮全部结束才清空**(比分事件携带 court 号,支持按场地追溯)
- **Captain(队长页)**:每队独立 PIN(`broadcast.captain_pins`)。三个标签页:
  ① 提交本轮名单(有比分后锁定);② **种子盲抽**——输入随机种子,为**对方**
  在合规池内(性别构成、非队长、排除前几轮已被抽中者)确定性抽出第五场人选,
  种子入日志可复现;③ 缺席登记与每轮顶替指定(三轮不重复校验)
- **Admin(管理页)**:`broadcast.admin_pin` 登录,四个标签:
  ⓪ **Live 主持人控制台**——观众页全部内容(场地图/对阵树/排名/盲抽/下一场)+
  逐场确认流:每块空闲场地显示下一场宣读词,主持人念完并看到选手上场后按
  "确认开打"(`match_started` 事件),该场变为进行中并等待志愿者录分;
  ① **对阵抽签(种子驱动)**——输入随机种子把 8 队随机分到 G1–G8,种子入日志可复现;
  ② **Lineups 状态总览**——按轮次监控每场对抗双方:名单是否提交、是否合规(缺项
  逐条列出)、盲抽是否完成及结果(含种子)、各队缺席/顶替状态;录入动作在队长页,
  Admin 只监督;
  ③ **Score correction**——更正志愿者错录的比分,以 `game_corrected` 补偿事件追加,
  原始录入保留在日志,若更正改变胜负判定则多余的后续局自动剔除

并发与存储:所有写入经 SQLite(WAL)串行化,每次追加后自动导出回 `events.jsonl`,
纯文本审计与 git 备份始终最新;赛前 CLI 写入的事件在 App 启动时自动导入。

## 可复现性验收标准

1. 同一 `config.yaml` + 同一 `events.jsonl`,任何机器重放结果逐字节一致
2. 删掉所有 CSV 快照,仅凭事件日志可全部重新导出
3. 取事件前缀重放 = 还原任意历史时刻的全场状态
4. 日志里的每个种子,离线重跑对应抽签函数得到相同结果

## 测试

```bash
python3 -m unittest discover -s tests -v
```
