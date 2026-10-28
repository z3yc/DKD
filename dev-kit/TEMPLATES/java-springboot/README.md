# 模板 · Java Spring Boot（骨架）

> ⚠️ **V1.0 状态：骨架 + 关键类，未做到"开箱即跑"。**
> 原因：Java 侧基线待你确认（见下表）。确认后我会补全为可运行工程。
> 在确认前，本模板的价值是**统一响应/异常处理/目录约定**这三件事，可单独取用。

## 待确认项（确认后即可补全）

| # | 项 | 当前默认 | 你的选择 |
|---|---|---|---|
| 1 | JDK 版本 | **Java 8**（沿用 DKD） | □ 8　□ 17　□ 21 |
| 2 | Lombok | **不用**（手写 getter/setter） | □ 不用　□ 用 |
| 3 | 构建工具 | Maven | □ Maven　□ Gradle |
| 4 | ORM | MyBatis（XML） | □ MyBatis　□ MyBatis-Plus　□ JPA |
| 5 | 是否需要多模块 | 单模块起步 | □ 单模块　□ 多模块 |

## 目录约定

```
src/main/java/<pkg>/
├─ common/            统一响应 R、BizException、GlobalExceptionHandler、常量、枚举
├─ config/            配置类（@ConfigurationProperties，从环境变量读）
├─ controller/        只做参数校验 + 调 Service + 包装 R；**禁写业务逻辑、禁直连 Mapper**
├─ service/  service/impl/
├─ mapper/   resources/mapper/*.xml
├─ domain/   domain/dto/  domain/vo/
└─ util/              仅放无状态纯函数
src/test/java/<pkg>/  与 main 目录结构对齐
```

## 已提供的关键类（可直接取用）

| 文件 | 作用 | 依赖 |
|---|---|---|
| `common/R.java` | 统一响应包装，对应 CONTRACTS/api-envelope.md | 无 |
| `common/BizException.java` | 业务异常 + 错误码类别，对应 CONTRACTS/error-codes.md | 无 |
| `common/GlobalExceptionHandler.java` | `@RestControllerAdvice` 全局兜底 | spring-web |

## Java 8 硬边界（若选 8 则必须遵守）

禁止：`var` / `record` / switch 表达式 / `List.of()` / `Map.of()` / 文本块 / `Optional.isEmpty()`。
原因：编译目标 1.8，混用会在 CI 才暴露，且报错信息不指向真实位置。

## 接入步骤

1. 用 Spring Initializr 或脚手架生成基础工程；
2. 覆盖目录结构为本模板约定；
3. 复制 `common/` 三个类，改包名；
4. 在 `application.yml` 中把配置项指向环境变量（`${APP_DB_DSN:}`）；
5. 写 `REVIEW.md`（用 `SPEC/templates/REVIEW.md.template`）与 `AGENTS.md`。

## 风格要点（详见 SPEC/03）

- 接口 `IXxxService` + 实现 `XxxServiceImpl`；
- 常量 `static final` 全大写，集中 `constant/` 包；
- 金额 `BigDecimal`，时间 `Date`/`LocalDateTime` 全项目统一一种；
- 审计字段四件套：`create_time` / `update_time` / `create_by` / `update_by`；
- 业务表 `tb_` 前缀，系统表 `sys_`（项目内统一，写进 AGENTS.md）；
- Controller 不得直接调 Mapper。
