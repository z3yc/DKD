-- ----------------------------
-- AI报表表
-- ----------------------------
DROP TABLE IF EXISTS tb_report;
CREATE TABLE tb_report (
    id              bigint(20)      NOT NULL AUTO_INCREMENT COMMENT '主键',
    report_type     varchar(20)     NOT NULL COMMENT '报表类型: daily/weekly',
    report_date     date            NOT NULL COMMENT '报表日期',
    total_revenue   bigint(20)      DEFAULT 0 COMMENT '总收入(分)',
    total_orders    int(11)         DEFAULT 0 COMMENT '成功订单数',
    total_devices   int(11)         DEFAULT 0 COMMENT '设备总数',
    online_devices  int(11)         DEFAULT 0 COMMENT '在线设备数',
    fault_devices   int(11)         DEFAULT 0 COMMENT '故障设备数',
    low_stock_devices int(11)       DEFAULT 0 COMMENT '库存不足设备数',
    top_products    text            COMMENT '热销商品JSON',
    top_nodes       text            COMMENT '高收入点位JSON',
    anomaly_alerts  text            COMMENT '异常告警JSON',
    ai_analysis     text            COMMENT 'AI分析报告全文',
    retry_count     int(11)         DEFAULT 0 COMMENT '重试次数',
    status          tinyint(1)      DEFAULT 0 COMMENT '状态: 0-生成中 1-成功 2-失败',
    create_time     datetime        DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time     datetime        DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_type_date (report_type, report_date)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COMMENT='AI报表表';

-- ----------------------------
-- 定时任务：日报（每天 08:00）
-- ----------------------------
INSERT INTO sys_job VALUES (100, 'AI运营日报', 'DEFAULT', 'reportTask.generateDailyReport()', '0 0 8 * * ?', '3', '1', '1', 'admin', sysdate(), '', null, '每日08:00自动生成AI运营日报');

-- ----------------------------
-- 定时任务：周报（每周一 08:00）
-- ----------------------------
INSERT INTO sys_job VALUES (101, 'AI运营周报', 'DEFAULT', 'reportTask.generateWeeklyReport()', '0 0 8 ? * MON', '3', '1', '1', 'admin', sysdate(), '', null, '每周一08:00自动生成AI运营周报');

-- ----------------------------
-- 菜单 SQL
-- ----------------------------
-- 一级菜单：AI报表（放在 manage 目录下）
INSERT INTO sys_menu VALUES (2000, 'AI报表', 0, 5, 'report', NULL, '', 1, 0, 'M', '0', '0', '', 'chart', 'admin', sysdate(), '', null, 'AI报表菜单');

-- 二级菜单
INSERT INTO sys_menu VALUES (2001, '日报查询', 2000, 1, 'daily', 'manage/report/daily', '', 1, 0, 'C', '0', '0', 'manage:report:query', '#', 'admin', sysdate(), '', null, '');
INSERT INTO sys_menu VALUES (2002, '周报查询', 2000, 2, 'weekly', 'manage/report/weekly', '', 1, 0, 'C', '0', '0', 'manage:report:query', '#', 'admin', sysdate(), '', null, '');
INSERT INTO sys_menu VALUES (2003, '报表列表', 2000, 3, 'list', 'manage/report/list', '', 1, 0, 'C', '0', '0', 'manage:report:list', '#', 'admin', sysdate(), '', null, '');

-- 按钮权限
INSERT INTO sys_menu VALUES (2004, '报表查询', 2001, 1, '', '', '', 1, 0, 'F', '0', '0', 'manage:report:query', '#', 'admin', sysdate(), '', null, '');
INSERT INTO sys_menu VALUES (2005, '报表列表', 2003, 1, '', '', '', 1, 0, 'F', '0', '0', 'manage:report:list', '#', 'admin', sysdate(), '', null, '');
INSERT INTO sys_menu VALUES (2006, '手动生成', 2003, 2, '', '', '', 1, 0, 'F', '0', '0', 'manage:report:generate', '#', 'admin', sysdate(), '', null, '');
