package com.dkd.manage.agent;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 智能体配置的「契约常量镜像」（排期 2-2，契约 §3.1/§4.4/§4.6）。
 *
 * <p><b>唯一事实源是 {@code dkd-agent/app/agent_defaults.py}（Lead 维护，Java 侧只读镜像）</b>：
 * 模型白名单、工具白名单、占位符白名单、参数区间/默认值、默认提示词都必须与它一致。
 * 一旦两边不一致，前端「恢复默认」与 Python 运行时行为就会在两个地方给出不同答案，
 * 而且这种漂移不会有任何报错。因此本类：
 * <ul>
 *   <li>常量集中在本类，Service 不散落字面量；</li>
 *   <li>默认提示词**不在 Java 源码里复制字符串**，而是放在
 *       {@code resources/agent/prompts/*.txt}，由一次性导出脚本从
 *       {@code agent_defaults.default_system_prompt()} 逐字节写出（chat/restock 已核对
 *       sha256 一致）。手抄 8KB 中文提示词必然出错，且出错了没人能一眼看出来；</li>
 *   <li>资源缺失时**构造即失败**（抛 IllegalStateException），不允许静默返回 null
 *       ——「恢复默认」悄悄变空会让运营以为提示词丢了。</li>
 * </ul>
 *
 * <p>排期 5-1 计划加 Java↔Python 常量比对脚本；在那之前，改这里必须同时通知 Lead。
 *
 * @author ruoyi
 * @date 2026-11-03
 */
public final class AgentConfigDefaults
{
    /** 对话载体 */
    public static final String RUNTIME_CHAT = "chat";

    /** 补货载体 */
    public static final String RUNTIME_RESTOCK = "restock";

    /** 诊断载体（不调 LLM，没有系统提示词） */
    public static final String RUNTIME_FAULT = "fault";

    /** 全部载体（顺序与 Python RUNTIMES 一致） */
    public static final List<String> RUNTIMES = Collections.unmodifiableList(
            Arrays.asList(RUNTIME_CHAT, RUNTIME_RESTOCK, RUNTIME_FAULT));

    /** 编码规则：小写字母开头，3~32 位小写字母/数字/下划线（与 Python CODE_PATTERN 一致） */
    public static final String CODE_PATTERN = "^[a-z][a-z0-9_]{2,31}$";

    /** 自定义场景码起始值（1~4 为内置场景，见 agent_tables.sql 列注释） */
    public static final int CUSTOM_SCENE_BASE = 101;

    /** 新增时 timeout_s 未传的默认值（与 DDL DEFAULT 60 一致） */
    public static final int DEFAULT_TIMEOUT_S = 60;

    /** 新增时 max_retries 未传的默认值（与 DDL DEFAULT 2 一致） */
    public static final int DEFAULT_MAX_RETRIES = 2;

    /** 新增时 enabled 未传的默认值（与 DDL DEFAULT 1 一致） */
    public static final int DEFAULT_ENABLED = 1;

    /** 模型白名单：配置里能填什么；空 = 走 DKD_AGENT_LLM_MODEL（未配置=现状） */
    public static final List<String> MODEL_WHITELIST = Collections.unmodifiableList(
            Arrays.asList("deepseek-chat", "deepseek-reasoner"));

    /**
     * 载体的工具白名单。chat = Python {@code TOOL_NAMES_BY_SCENE} 的并集（已排序）；
     * restock/fault 首期不开放工具子集（必须为空）。
     */
    public static final Map<String, List<String>> TOOL_WHITELIST_BY_RUNTIME = buildToolWhitelist();

    /** 提示词允许出现的占位符白名单（chat 的三个是 _BASE_PROMPT 里真实存在且会被渲染的） */
    public static final Map<String, List<String>> ALLOWED_PLACEHOLDERS = buildPlaceholders();

    /** 各载体 params_json 的字段规格（区间/默认值/类型，与 Python PARAM_SPECS 一致） */
    public static final Map<String, List<ParamSpec>> PARAM_SPECS = buildParamSpecs();

    /** 各载体默认提示词（chat/restock；fault 不调 LLM，故没有键） */
    public static final Map<String, String> DEFAULT_PROMPTS = buildDefaultPrompts();

    /** 载体中文名（meta 接口 / 前端下拉展示） */
    private static final Map<String, String> RUNTIME_LABELS = buildRuntimeLabels();

    /** 本期允许新增的载体（Q1：首期只放开 chat） */
    private static final Set<String> CREATABLE_RUNTIMES = Collections.unmodifiableSet(
            new LinkedHashSet<String>(Arrays.asList(RUNTIME_CHAT)));

    /** 系统提示词长度下限（chat/restock） */
    public static final int PROMPT_MIN_LENGTH = 100;

    /** 系统提示词长度上限（chat/restock） */
    public static final int PROMPT_MAX_LENGTH = 8000;

    private AgentConfigDefaults()
    {
    }

    /**
     * 是否为合法载体。
     *
     * @param runtime 载体编码
     * @return true 表示 chat/restock/fault 之一
     */
    public static boolean isRuntime(String runtime)
    {
        return runtime != null && RUNTIMES.contains(runtime);
    }

    /**
     * 该载体是否允许新增（本期只有 chat）。
     *
     * @param runtime 载体编码
     * @return true 表示可新增
     */
    public static boolean isCreatable(String runtime)
    {
        return CREATABLE_RUNTIMES.contains(runtime);
    }

    /**
     * 该载体的参数规格；未知载体返回空列表（调用方据此判定「没有可配参数」）。
     *
     * @param runtime 载体编码
     * @return 参数规格列表（顺序稳定）
     */
    public static List<ParamSpec> paramSpecs(String runtime)
    {
        List<ParamSpec> specs = PARAM_SPECS.get(runtime);
        return specs == null ? Collections.<ParamSpec>emptyList() : specs;
    }

    /**
     * 该载体的默认参数（深拷贝为普通 LinkedHashMap，防止调用方改坏常量）。
     *
     * @param runtime 载体编码
     * @return key → 默认值
     */
    public static Map<String, Object> defaultParams(String runtime)
    {
        Map<String, Object> params = new LinkedHashMap<String, Object>();
        for (ParamSpec spec : paramSpecs(runtime))
        {
            params.put(spec.getKey(), spec.getDefault());
        }
        return params;
    }

    /**
     * 该载体的默认提示词。
     *
     * @param runtime 载体编码
     * @return 默认提示词；fault/未知载体返回 null（表示没有提示词）
     */
    public static String defaultPrompt(String runtime)
    {
        return DEFAULT_PROMPTS.get(runtime);
    }

    /**
     * meta 接口的 runtimes 段（契约 §4.6）。
     *
     * @return 载体描述列表（code/label/creatable）
     */
    public static List<Map<String, Object>> runtimeDescriptors()
    {
        List<Map<String, Object>> descriptors = new ArrayList<Map<String, Object>>();
        for (String runtime : RUNTIMES)
        {
            Map<String, Object> item = new LinkedHashMap<String, Object>();
            item.put("code", runtime);
            item.put("label", RUNTIME_LABELS.get(runtime));
            item.put("creatable", Boolean.valueOf(isCreatable(runtime)));
            descriptors.add(item);
        }
        return descriptors;
    }

    private static Map<String, List<String>> buildToolWhitelist()
    {
        Map<String, List<String>> whitelist = new LinkedHashMap<String, List<String>>();
        // chat：与 Python TOOL_NAMES_BY_SCENE 的并集一致（字典序，见 agent_defaults.py 的 sorted）
        whitelist.put(RUNTIME_CHAT, Collections.unmodifiableList(Arrays.asList(
                "build_device_diagnosis",
                "find_devices",
                "get_machine_inventory",
                "get_sales_summary",
                "list_device_anomalies",
                "list_inflight_work_orders",
                "list_restock_suggestions",
                "search_device_history")));
        whitelist.put(RUNTIME_RESTOCK, Collections.<String>emptyList());
        whitelist.put(RUNTIME_FAULT, Collections.<String>emptyList());
        return Collections.unmodifiableMap(whitelist);
    }

    private static Map<String, List<String>> buildPlaceholders()
    {
        Map<String, List<String>> placeholders = new LinkedHashMap<String, List<String>>();
        placeholders.put(RUNTIME_CHAT, Collections.unmodifiableList(
                Arrays.asList("focus", "tool_list", "write_guidance")));
        placeholders.put(RUNTIME_RESTOCK, Collections.unmodifiableList(Arrays.asList("fmin", "fmax")));
        placeholders.put(RUNTIME_FAULT, Collections.<String>emptyList());
        return Collections.unmodifiableMap(placeholders);
    }

    private static Map<String, List<ParamSpec>> buildParamSpecs()
    {
        Map<String, List<ParamSpec>> specs = new LinkedHashMap<String, List<ParamSpec>>();

        List<ParamSpec> restock = new ArrayList<ParamSpec>();
        restock.add(new ParamSpec("quantile", "float", Double.valueOf(0.50), Double.valueOf(0.99), Double.valueOf(0.75)));
        restock.add(new ParamSpec("coverage_days", "float", Double.valueOf(1.0), Double.valueOf(14.0), Double.valueOf(2.0)));
        restock.add(new ParamSpec("service_factor", "float", Double.valueOf(0.5), Double.valueOf(3.0), Double.valueOf(1.2)));
        restock.add(new ParamSpec("factor_min", "float", Double.valueOf(0.50), Double.valueOf(2.00), Double.valueOf(0.7)));
        restock.add(new ParamSpec("factor_max", "float", Double.valueOf(0.50), Double.valueOf(2.00), Double.valueOf(1.5)));
        restock.add(new ParamSpec("batch_size", "int", Integer.valueOf(5), Integer.valueOf(100), Integer.valueOf(20)));
        specs.put(RUNTIME_RESTOCK, Collections.unmodifiableList(restock));

        List<ParamSpec> fault = new ArrayList<ParamSpec>();
        fault.add(new ParamSpec("window_hours", "int", Integer.valueOf(1), Integer.valueOf(168), Integer.valueOf(24)));
        fault.add(new ParamSpec("confidence_order_threshold", "int", Integer.valueOf(1), Integer.valueOf(10), Integer.valueOf(2)));
        specs.put(RUNTIME_FAULT, Collections.unmodifiableList(fault));

        specs.put(RUNTIME_CHAT, Collections.<ParamSpec>emptyList());
        return Collections.unmodifiableMap(specs);
    }

    private static Map<String, String> buildDefaultPrompts()
    {
        Map<String, String> prompts = new LinkedHashMap<String, String>();
        // 与 dkd-agent/app/prompts/assistant_chat.py::BASE_PROMPT 逐字节一致（sha256 已核对）
        prompts.put(RUNTIME_CHAT, loadText("agent/prompts/default-chat-prompt.txt"));
        // 与 dkd-agent/app/prompts/restock_calibration.py::SYSTEM_PROMPT 逐字节一致
        prompts.put(RUNTIME_RESTOCK, loadText("agent/prompts/default-restock-prompt.txt"));
        return Collections.unmodifiableMap(prompts);
    }

    private static Map<String, String> buildRuntimeLabels()
    {
        Map<String, String> labels = new LinkedHashMap<String, String>();
        labels.put(RUNTIME_CHAT, "对话载体");
        labels.put(RUNTIME_RESTOCK, "补货载体");
        labels.put(RUNTIME_FAULT, "诊断载体");
        return Collections.unmodifiableMap(labels);
    }

    /**
     * 读取 classpath 文本（UTF-8，保留原始换行）。
     *
     * @param path 资源路径
     * @return 文本内容
     * @throws IllegalStateException 资源缺失或读取失败（静默返回空串会让「恢复默认」悄悄失效）
     */
    private static String loadText(String path)
    {
        InputStream in = AgentConfigDefaults.class.getClassLoader().getResourceAsStream(path);
        if (in == null)
        {
            throw new IllegalStateException("智能体默认提示词资源缺失：" + path);
        }
        try
        {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int read;
            while ((read = in.read(buffer)) != -1)
            {
                out.write(buffer, 0, read);
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
        catch (IOException e)
        {
            throw new IllegalStateException("读取智能体默认提示词失败：" + path, e);
        }
        finally
        {
            try
            {
                in.close();
            }
            catch (IOException ignored)
            {
                // 只读 classpath 流，关闭失败不影响已读取的内容
            }
        }
    }

    /**
     * 单个业务参数的规格（meta.paramSpecs 的元素，契约 §4.6）。
     *
     * <p>getter 名决定 JSON 键名：{@code getDefault()} → {@code "default"}（前端动态表单直接用）。
     */
    public static final class ParamSpec
    {
        private final String key;

        private final String type;

        private final Object min;

        private final Object max;

        private final Object defaultValue;

        ParamSpec(String key, String type, Object min, Object max, Object defaultValue)
        {
            this.key = key;
            this.type = type;
            this.min = min;
            this.max = max;
            this.defaultValue = defaultValue;
        }

        public String getKey()
        {
            return key;
        }

        public String getType()
        {
            return type;
        }

        public Object getMin()
        {
            return min;
        }

        public Object getMax()
        {
            return max;
        }

        public Object getDefault()
        {
            return defaultValue;
        }

        /**
         * @return 区间下限（数值化）
         */
        public double minAsDouble()
        {
            return ((Number) min).doubleValue();
        }

        /**
         * @return 区间上限（数值化）
         */
        public double maxAsDouble()
        {
            return ((Number) max).doubleValue();
        }

        /**
         * @return true 表示该参数必须是整数
         */
        public boolean isIntType()
        {
            return "int".equals(type);
        }
    }
}
