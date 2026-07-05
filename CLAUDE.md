# hollow — AI Research Browser

免费开源(AGPL-3.0)的深度研究 API 服务:多源搜索召回 → 三档升级全文爬取 → 正文净化 → 可溯源 Evidence Pack,之上生长 Semantic Reader 阅读体验。这是 v2(第二次落地);v1 因"自研了该借力的基建"而烂尾。

**沟通语言:中文。** 用户偏好:先研究清楚再动手;系统性学习用 Opus workflow(并行研读 + 对抗复核 + 综合);重决策先讨论、给出分析后等拍板。

## 必读文档(按优先级)

1. `docs/references/AI_Research_Browser_落地项目书_v2.md` — 工程蓝图:选型、API 契约、阶段计划 P1-P5、风险册
2. `docs/memory/2026-07-05-session-01.md` — **上一会话的完整记忆**:所有决策、实测结论、待拍板事项
3. `docs/design/` — 01 搜索源注册表 / 02 裁剪分析 / 03 第一个 API 研究
4. `docs/research/searxng/00-overview.md`、`docs/research/scrapling/00-overview.md` — 两大基建的源码研读总览(各带 10 篇子系统笔记,全部标注 文件:行号)

## 四条不可协商底线(v2 项目书 §8)

1. 成功声明必须来自实测返回,而非意图
2. **禁止静默丢弃**(engines_failed / fetch_status 全显式;集合差集对账兜底)
3. 一切内容可溯源(engine / fetched_at / url)
4. 阈值必须校准后上线

## 关键架构决策(已在对话中定案)

- **不依赖 Docker**:SearXNG 以本地 Python 进程直跑 vendor 源码(已实测跑通),或后续演进为进程内库集成;**不做语言转写/重写引擎适配器**(那是 v1 的死法,249 个适配器的维护债必须留给上游社区)
- **全栈 Python**:FastAPI 网关 + SearXNG(搜索)+ Scrapling(三档抓取)+ trafilatura(净化)
- 自研边界只有三样:API 编排层、Evidence Pack 组装、阅读器前端
- SearXNG 的 simple 前端/preferences 界面**不是**要复用的资产(要被 P4 自研阅读器取代),但其"设置数据模型"(引擎启停/分类/偏好存差异/可导出)值得借鉴

## 本地环境首启(给下一会话的我)

**第一件事:先把仓库 clone 下来,再干别的。**

```bash
git clone https://github.com/evo-Vonish/hollow.git
cd hollow
git checkout claude/new-project-setup-1dzma1   # 所有工作都在这个分支
```

clone 完先读 `docs/memory/2026-07-05-session-01.md` 恢复全部上下文(决策、实测结论、待拍板事项),然后按下方「环境注意」重验沙箱限制是否在本地消失。

## 仓库结构

```
vendor/searxng/    SearXNG 完整快照 @ a643858(未修改;见 vendor/UPSTREAM.md)
vendor/scrapling/  Scrapling v0.4.10 快照 @ 8e7bc99(未修改)
searxng/settings.yml   我们的 SearXNG 最小部署配置(已实测)
data/engine_registry.yaml  343 个搜索源注册表(tier T0-T3 × 10 类型)
tools/gen_engine_registry.py  注册表生成器(改分类后重跑,未分类会报错)
docs/research/     两大基建源码研读笔记(21 篇,Opus workflow 产出)
docs/design/       设计文档(注册表/裁剪/第一个API)
docs/memory/       会话记忆
```

## 快速启动 SearXNG(本地进程)

```bash
python3 -m venv .venv-searx && .venv-searx/bin/pip install -r vendor/searxng/requirements.txt
SEARXNG_SETTINGS_PATH=$PWD/searxng/settings.yml PYTHONPATH=$PWD/vendor/searxng \
  .venv-searx/bin/python -m searx.webapp   # → http://127.0.0.1:8888/healthz
# 调用:POST /search, form-urlencoded, format=json + 浏览器风格请求头(UA/Accept含text/html/Accept-Language)
```

## 环境注意(云沙箱 → 本地的差异)

上一会话在云沙箱(egress 代理)中实测,**以下沙箱限制在本地直连环境应重新验证,大概率消失**:
- Scrapling 静态档在沙箱须 `impersonate=None`(代理重置模拟 TLS);**本地直连应恢复默认 `impersonate="chrome"`**
- 浏览器档在沙箱因 Chromium 版本不匹配 + 代理未接被推迟;本地 `scrapling install`(或 `playwright install chromium`)后应可用
- 中文引擎(baidu/sogou/bilibili)与 DDG 在数据中心 IP 实测可用;brave/startpage/mojeek 被封 —— **本地家用 IP 结果可能不同,值得重测**

## 工作纪律

- 分支:`claude/new-project-setup-1dzma1`;每轮工作提交并推送(stop-hook 会检查)
- vendor/ 一行不改;改动只发生在我们自己的目录
- 待拍板事项见 memory 文档末尾——动手前先确认用户已拍板
