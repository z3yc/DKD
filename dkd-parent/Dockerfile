# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# dkd-admin 镜像（管理后台：Java 8 / Spring Boot 2.5.15 / Maven 多模块）
#
# 构建上下文必须是 `dkd-parent/`（不是在仓库根）：
#   `mvn -pl dkd-admin -am` 需要父 POM 与 7 个兄弟模块同时在场，否则解析不到模块。
# 用法（由 deploy/docker/docker-compose.yml 调用）：
#   docker build -t dkd/dkd-admin:local --build-arg MAVEN_MIRROR=https://maven.aliyun.com/repository/public dkd-parent
# ---------------------------------------------------------------------------
FROM maven:3.8.8-eclipse-temurin-8 AS build

# 国内网络加速（不写进仓库，构建期注入）；留空则用 Maven Central
ARG MAVEN_MIRROR
# 注意：settings.xml 写在 /root/.m2 下，而下面的缓存挂载必须只覆盖 /root/.m2/repository，
# 否则挂载会把 settings.xml 盖掉（BuildKit 的 cache mount 首次为空）→ 镜像加速静默失效。
RUN if [ -n "$MAVEN_MIRROR" ]; then \
      mkdir -p /root/.m2 && \
      printf '%s' "<settings><mirrors><mirror><id>cn</id><mirrorOf>central</mirrorOf><name>cn</name><url>$MAVEN_MIRROR</url></mirror></mirrors></settings>" \
        > /root/.m2/settings.xml; \
    fi

WORKDIR /src
COPY . .

# -DskipTests：镜像构建只产出可运行包；测试由 CI/本地门禁负责（AGENTS §8），
#   不在镜像构建里跑（避免把测试时长和外部依赖带进部署流水线）。
RUN --mount=type=cache,target=/root/.m2/repository \
    mvn -B -DskipTests -pl dkd-admin -am package

# ---------- 运行期 ----------
FROM eclipse-temurin:8-jre-jammy

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    JAVA_OPTS="-Xms512m -Xmx1024m -XX:+UseSerialGC"

# curl 只为 HEALTHCHECK 用（基础镜像不带）；不装 wget/其他工具，保持镜像最小
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /src/dkd-admin/target/dkd-admin.jar /app/app.jar
# RuoYi 的 logback 写到工作目录下的 logs/（见 logback-spring.xml），提前建好并挂卷持久化
RUN mkdir -p /app/logs

EXPOSE 8080
# /captchaImage 是 RuoYi 唯一无需鉴权的轻量端点：能返回 200 说明 Spring 上下文已就绪
# （比只看端口监听更可信：端口在上下文启动前就可能被 Tomcat 占用）
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8080/captchaImage >/dev/null || exit 1

# exec 形式保证 java 是 PID 1 的直系子进程，能收到 docker stop 的 SIGTERM（优雅停机）
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
