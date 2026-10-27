package com.dkd.app.common.util;

import com.dkd.app.common.token.TokenObject;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtBuilder;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Date;

/**
 * 运维端 JWT 签发与校验。
 *
 * <p>为什么改成 Spring 组件、不再用静态方法：密钥必须来自环境变量（AGENTS §7.1），
 * 而静态工具类拿不到 {@code @Value}；早期版本把密钥写死在源码里（HS256 密钥 "dkd"），
 * 任何人拿到仓库即可伪造任意 userId 的令牌，等于整条移动端鉴权形同虚设。
 *
 * <p>为什么构造时就校验强度并直接抛异常：弱密钥启动即失败，比"跑起来但谁都能伪造"安全得多；
 * 这类配置错误在生产环境静默降级是最危险的失败模式。
 *
 * @author ruoyi
 * @date 2026-10-21
 */
@Component
public class JWTUtil {

    /**
     * HS256 要求密钥至少与哈希输出等长（32 字节）才有实际强度，低于此值视为配置错误
     */
    private static final int MIN_SECRET_BYTES = 32;

    /**
     * 默认有效期 12 小时。原实现写死 7 天且无吊销机制，令牌一旦泄漏窗口过长
     */
    private static final long DEFAULT_EXPIRE_MINUTES = 720L;

    private final byte[] secret;

    private final long expireMinutes;

    public JWTUtil(@Value("${dkd.jwt.secret}") String secret,
                   @Value("${dkd.jwt.expire-minutes:720}") long expireMinutes) {
        if (secret == null || secret.getBytes(StandardCharsets.UTF_8).length < MIN_SECRET_BYTES) {
            throw new IllegalStateException(
                    "dkd.jwt.secret 未配置或长度不足 " + MIN_SECRET_BYTES + " 字节，拒绝启动（AGENTS §7.1：禁止弱密钥）");
        }
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
        this.expireMinutes = expireMinutes > 0 ? expireMinutes : DEFAULT_EXPIRE_MINUTES;
    }

    /**
     * 签发 JWT。
     *
     * @param tokenObject 令牌载荷（userId/mobile/loginType/userName）
     * @return 紧凑格式的 JWT
     * @throws IOException 载荷序列化失败
     */
    public String createJWTByObj(TokenObject tokenObject) throws IOException {
        long now = System.currentTimeMillis();
        JwtBuilder builder = Jwts.builder().setHeaderParam("typ", "JWT")
                .setHeaderParam("alg", "HS256")
                .addClaims(JsonUtil.convertToMap(tokenObject))
                // 补上签发/过期时间：原实现只有过期时间，缺 iat 无法判断令牌年龄
                .setIssuedAt(new Date(now))
                .setExpiration(new Date(now + expireMinutes * 60L * 1000L))
                .signWith(SignatureAlgorithm.HS256, secret);
        return builder.compact();
    }

    /**
     * 校验 JWT 并取出员工 id。
     *
     * @param token 请求头里的令牌（不含 Bearer 前缀）
     * @return 员工 id
     * @throws IOException 解析失败（签名不对、过期、格式非法都会抛）
     */
    public int decode(String token) throws IOException {
        Claims claims = Jwts.parser().setSigningKey(secret).parseClaimsJws(token).getBody();
        Object userId = claims.get("userId");
        if (!(userId instanceof Number)) {
            // 缺 userId 的令牌即使签名正确也不该放行，否则下游 EmpHolder 会拿到 null
            throw new IllegalArgumentException("token 缺少 userId");
        }
        return ((Number) userId).intValue();
    }
}
