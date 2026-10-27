package com.dkd.app.service.impl;

import cn.hutool.core.util.ObjectUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.dkd.app.common.exception.LogicException;
import com.dkd.app.common.token.TokenObject;
import com.dkd.app.common.util.JWTUtil;
import com.dkd.app.common.util.SmsTemplate;
import com.dkd.app.domain.Emp;
import com.dkd.app.domain.dto.LoginDto;
import com.dkd.app.domain.vo.LoginVo;
import com.dkd.app.mapper.EmpMapper;
import com.dkd.app.service.EmpService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.time.Duration;

/**
 * 人员列表Service业务层处理
 *
 * <p>2026-10-21 安全修复（审计 2026-10-21 F3）：
 * 原实现把验证码写死为常量 "12345"，短信发送被注释掉，且 /user-service/user/{id}
 * 无需令牌即可返回手机号——三者叠加等于"枚举手机号即可接管任意员工账号"。
 * 现在：验证码必须随机、必须真实下发（或显式开启 mock 且只在开发环境）、
 * 同一手机号 60 秒内只能请求一次。
 *
 * @author itheima
 * @date 2024-05-16
 */
@Slf4j
@Service
public class EmpServiceImpl extends ServiceImpl<EmpMapper, Emp> implements EmpService {

    /**
     * 验证码 Redis 前缀，5 分钟有效
     */
    private static final String SMS_CODE_KEY_PREFIX = "dkd.sms:";

    /**
     * 发送频控前缀：60 秒内重复请求直接拒绝，避免短信被刷（费用与骚扰）
     */
    private static final String SMS_LOCK_KEY_PREFIX = "dkd.sms:lock:";

    @Autowired
    private StringRedisTemplate redisTemplate;

    @Autowired
    private SmsTemplate smsTemplate;

    @Autowired
    private JWTUtil jwtUtil;

    /**
     * 短信模拟开关：仅用于没有短信资质的本地/联调环境。
     *
     * <p>为什么默认 false：默认开启等于"验证码不进用户手机"，是原缺陷的翻版；
     * 只有显式配置 DKD_SMS_MOCK=true 才走模拟，且模拟模式下代码仍随机生成、不落日志。
     */
    @Value("${dkd.sms.mock:false}")
    private boolean smsMock;

    @Override
    public void sendSms(String mobile) {
        if (StrUtil.isBlank(mobile)) {
            throw new LogicException("手机号不能为空");
        }
        if (Boolean.TRUE.equals(redisTemplate.hasKey(SMS_LOCK_KEY_PREFIX + mobile))) {
            throw new LogicException("验证码发送过于频繁，请稍后再试");
        }
        if (!smsMock && (StrUtil.isBlank(smsTemplate.getKey()) || StrUtil.isBlank(smsTemplate.getSecret()))) {
            // fail-closed：没配短信凭据时宁可登录不可用，也不退回固定验证码
            throw new LogicException("短信服务未配置，暂时无法登录，请联系管理员");
        }
        // 6 位随机数字（原实现是常量 "12345"，任何人拿到手机号即可登录）
        String code = RandomUtil.randomNumbers(6);
        redisTemplate.opsForValue().set(SMS_CODE_KEY_PREFIX + mobile, code, Duration.ofMinutes(5));
        redisTemplate.opsForValue().set(SMS_LOCK_KEY_PREFIX + mobile, "1", Duration.ofSeconds(60));
        if (smsMock) {
            // 模拟模式只记"发过"，绝不把验证码写进日志（日志会被集中采集）
            log.warn("短信处于模拟模式，未真实下发 mobile={}（DKD_SMS_MOCK=true）", mobile);
            return;
        }
        smsTemplate.sendSms(mobile, code);
    }

    /**
     * 登录
     *
     * @param req 手机号 + 验证码
     * @return 令牌与员工信息
     * @throws IOException 令牌签发失败
     */
    @Override
    public LoginVo login(LoginDto req) throws IOException {
        // 1. 校验验证码：只比对 Redis 中的随机值，比对通过后立即删除（一码一次）
        String redisCode = redisTemplate.opsForValue().get(SMS_CODE_KEY_PREFIX + req.getMobile());
        if (StrUtil.isBlank(redisCode) || !StrUtil.equals(req.getCode(), redisCode)) {
            throw new LogicException("验证码错误");
        }

        // 2. 比对手机号
        LambdaQueryWrapper<Emp> qw = new LambdaQueryWrapper<>();
        qw.eq(Emp::getMobile, req.getMobile());
        Emp emp = this.getOne(qw);
        if (ObjectUtil.isEmpty(emp)) {
            throw new LogicException("手机号非法");
        }

        // 3. 登录成功，制作 token 返回结果
        LoginVo resp = new LoginVo();
        resp.setSuccess(true);
        resp.setRoleCode(emp.getRoleCode());
        resp.setUserName(emp.getUserName());
        resp.setUserId(emp.getId());
        // 区域可能为空（未分配区域的新员工），直接 toString 会 NPE 把登录打成 500
        resp.setRegionId(emp.getRegionId() == null ? null : emp.getRegionId().toString());
        resp.setMsg("登录成功");
        TokenObject tokenObject = new TokenObject();
        tokenObject.setUserId(emp.getId());
        tokenObject.setMobile(emp.getMobile());
        tokenObject.setLoginType(req.getLoginType());
        tokenObject.setUserName(emp.getUserName());
        resp.setToken(jwtUtil.createJWTByObj(tokenObject));
        // 是否为维修员
        if ("1003".equals(emp.getRoleCode())) {
            resp.setRepair(true);
        }

        // 删除 Redis 中的验证码，防止重放
        redisTemplate.delete(SMS_CODE_KEY_PREFIX + req.getMobile());
        return resp;
    }

}
