# L0-04 · 分支策略、提交约定、版本与 CHANGELOG

## 1. 分支策略（轻量版，单人/小团队）

```
main        永远可发布；只接受 PR 合入，禁止直推
feat/<短名> 新功能      ← 从 main 切
fix/<短名>  缺陷修复
chore/<短名> 依赖、配置、文档
hotfix/<短名> 线上紧急修复 ← 从 main 的 tag 切，修完同时回 main
```

**规则**：
- 分支名 kebab-case，不超过 40 字符，带上问题编号更好（`feat/123-restock-plan`）；
- **分支存活不超过 5 个工作日**；超过说明拆得不够小 —— 长分支的合并冲突成本是非线性的；
- 合入方式用 **squash merge**，保证 main 上一个 PR 一个提交，便于 revert；
- **revert 优先于 hotfix**：线上出问题时先 revert 恢复服务，再慢慢修（这是 Anthropic 那套流程的底层假设，也是本体系性能/安全冗余的来源）。

## 2. 提交信息（Conventional Commits 精简版）

```
<type>(<scope>): <subject>

<body>      可选：为什么（不是做了什么）
<footer>    可选：BREAKING CHANGE / Closes #123
```

| type | 用于 |
|---|---|
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `refactor` | 重构，行为不变 |
| `perf` | 性能优化 |
| `docs` | 文档 |
| `test` | 测试 |
| `build` / `ci` | 构建与流水线 |
| `chore` | 其他（依赖、格式化） |

**subject 规则**：祈使句、不超过 72 字符、结尾无句号、首字母小写（英文）或直接中文。

```
✅ feat(restock): 补货建议支持按区域过滤
❌ 修改了补货建议的一些逻辑，然后加了过滤
```

**一个 PR 一个 type**。混了多种类型说明 PR 太大，拆开 —— 这同时让 revert 粒度变得可控。

## 3. 版本号（Semantic Versioning）

`MAJOR.MINOR.PATCH`，存于根目录 `VERSION` 单行文件。

- **MAJOR**：不兼容变更（对外接口、DB 结构、配置项的破坏性变更）
- **MINOR**：向后兼容的新功能
- **PATCH**：向后兼容的修复

**版本号变更由 PR 打标签决定**，不由人随手改：
- 存在 `feat` → MINOR+1
- 只有 `fix`/`perf` → PATCH+1
- 提交信息含 `BREAKING CHANGE:` → MAJOR+1

打 tag：`v1.2.3`，与 `VERSION` 保持一致。

## 4. CHANGELOG

倒序，每个版本一段。**CHANGELOG 是给人看的，不是 git log 的复制品** —— 只写使用者能感知的变化。

```markdown
# Changelog

## [1.2.0] - 2026-10-27
### Added
- 补货建议支持按区域过滤（#123）
### Fixed
- 修复同一设备 24h 内重复建单的问题（#127）
### Changed
- **BREAKING**: `POST /agent/restock` 的 `region_id` 由可选改为必填，迁移方式见 docs/decisions/

## [1.1.0] - 2026-10-10
...
```

**`BREAKING` 必须写迁移方式**，只写"变了"不写"怎么办"等于没写。

## 5. PR 模板（`.github/pull_request_template.md`）

```markdown
## 做了什么
（一段话，说明意图，不是列 diff）

## 风险等级
- [ ] Low  文档/文案/仅内部/flag 后新增 → 无需人工批准
- [ ] Medium 普通产品变更/敏感区（缓存、DDL、鉴权、SQL）
- [ ] High  写入链路/跨系统集成/CI 与部署脚本

## 如何验证（Testing）
- [ ] 命令：______
- [ ] 输出证据：（贴测试结果/运行输出/截图，不是"已测试"）

## 如何回滚（How to revert）
- [ ] 方式：revert / flag 关闭 / 迁移回滚脚本：______

## 检查项
- [ ] 自动化检查（lint/type/test/build）全绿
- [ ] 新增/更新了测试，覆盖异常与边界
- [ ] 未引入新的 TODO 或已带负责人与日期
- [ ] 涉及 DB/接口变更的，DDL 与文档已归档
```

> 后两项（Testing / How to revert）来自 Anthropic 的 PR 模板三问。**经验数据**：强制回答"如何回滚"会让 revert 成本显著下降，因为它倒逼作者把变更做小、做可关。

## 6. 提交前的强制门禁（pre-commit）

```yaml
# .pre-commit-config.yaml（Python 项目示例）
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff        # args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: detect-private-key      # 防密钥入库
      - id: check-added-large-files
      - id: end-of-file-fixer
      - id: trailing-whitespace
```

**`detect-private-key` 必须开** —— 密钥一旦进历史，清理成本是清理代码本身的十倍（需要轮换 + 重写历史）。
