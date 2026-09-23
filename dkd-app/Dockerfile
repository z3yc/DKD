# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# dkd-app 镜像（运维移动端：Spring Boot 2.7.3 / MyBatis-Plus / 端口 9070）
#
# 与 dkd-parent 的差异（实测，勿凭 POM 字面判断）：
#   POM 里写了 `maven.compiler.source/target=11`，但 spring-boot-starter-parent 的
#   `java.version=1.8` 优先生效 —— 产物经 `od -tx1` 核对为 **major 52（Java 8 字节码）**。
#   因此这里与 dkd-admin 统一用 JDK 8 构建与运行（JRE 11 也能跑，但没必要引入第二种运行时）。
# 用法：
#   docker build -t dkd/dkd-app:local --build-arg MAVEN_MIRROR=https://maven.aliyun.com/repository/public dkd-app
# ---------------------------------------------------------------------------
FROM maven:3.8.8-eclipse-temurin-8 AS build

ARG MAVEN_MIRROR
# 同 dkd-admin：settings.xml 与缓存挂载必须错开路径，否则镜像配置被挂载盖掉
RUN if [ -n "$MAVEN_MIRROR" ]; then \
      mkdir -p /root/.m2 && \
      printf '%s' "<settings><mirrors><mirror><id>cn</id><mirrorOf>central</mirrorOf><name>cn</name><url>$MAVEN_MIRROR</url></mirror></mirrors></settings>" \
        > /root/.m2/settings.xml; \
    fi

WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.m2/repository \
    mvn -B -DskipTests clean package

# ---------- 运行期 ----------
FROM eclipse-temurin:8-jre-jammy

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    JAVA_OPTS="-Xms256m -Xmx512m"

WORKDIR /app
COPY --from=build /src/target/dkd-app.jar /app/app.jar
RUN mkdir -p /app/logs

EXPOSE 9070
# 该应用没有公开的健康检查端点，故用 TCP 探测：端口能握手说明 `server.port` 已绑定，
# 排除“容器起来了但应用没起来”的假健康（比不做 healthcheck 强，已在 README 记为可加固项）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=6 \
  CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/9070' || exit 1

ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
