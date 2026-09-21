# Project: 帝可得管理系统 (DKD Management System)

Java 8 + Spring Boot 后端管理系统，基于若依(RuoYi)框架，用于管理自动售货机业务。

## Tech Stack
- Java 8, Spring Boot, MyBatis
- MySQL, Druid connection pool
- JWT authentication
- Swagger API docs

## Modules
- `dkd-admin`: 应用入口
- `dkd-framework`: 框架配置
- `dkd-system`: 系统管理
- `dkd-manage`: 核心业务（点位、员工、售货机、货道、商品、订单、工单、库存）
- `dkd-common`: 公共工具 + AI 模块
- `dkd-quartz`: 定时任务
- `dkd-generator`: 代码生成器

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
