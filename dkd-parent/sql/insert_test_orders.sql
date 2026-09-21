-- 插入测试订单数据
-- 请确保tb_order表已存在

-- 清空现有测试数据（可选）
-- DELETE FROM tb_order WHERE id IN (1,2,3,4,5);

-- 插入测试订单数据
INSERT INTO tb_order (
    id, order_no, third_no, inner_code, channel_code, sku_id, sku_name, 
    class_id, status, amount, price, pay_type, pay_status, bill, addr, 
    region_id, region_name, business_type, partner_id, open_id, node_id, 
    node_name, cancel_desc, create_time, update_time
) VALUES 
-- 待支付订单（可以取消）
(1, 'ORD202411190001', 'WX202411190001', 'VM001', 'CH001', 1, '可口可乐', 
 1, 0, 300, 300, '2', 0, 270, '北京市朝阳区某某大厦1楼', 
 1, '北京市', 1, 1, 'openid123', 1, 
 '某某大厦', NULL, '2024-11-19 10:30:00', '2024-11-19 10:30:00'),

-- 支付完成订单
(2, 'ORD202411190002', 'ALI202411190002', 'VM002', 'CH002', 2, '雪碧', 
 1, 1, 250, 250, '1', 1, 225, '上海市浦东新区某某商场2楼', 
 2, '上海市', 1, 2, 'openid456', 2, 
 '某某商场', NULL, '2024-11-19 11:15:00', '2024-11-19 11:20:00'),

-- 出货成功订单（可以退款）
(3, 'ORD202411190003', 'WX202411190003', 'VM003', 'CH003', 3, '农夫山泉', 
 2, 2, 200, 200, '2', 1, 180, '广州市天河区某某写字楼3楼', 
 3, '广州市', 1, 3, 'openid789', 3, 
 '某某写字楼', NULL, '2024-11-19 12:00:00', '2024-11-19 12:30:00'),

-- 出货失败订单
(4, 'ORD202411190004', 'ALI202411190004', 'VM004', 'CH004', 4, '康师傅绿茶', 
 2, 3, 350, 350, '1', 0, 315, '深圳市南山区某某科技园4楼', 
 4, '深圳市', 1, 4, 'openid101', 4, 
 '某某科技园', NULL, '2024-11-19 13:45:00', '2024-11-19 14:00:00'),

-- 已取消订单
(5, 'ORD202411190005', 'WX202411190005', 'VM005', 'CH005', 5, '统一冰红茶', 
 2, 4, 300, 300, '2', 0, 270, '杭州市西湖区某某大学5楼', 
 5, '杭州市', 1, 5, 'openid102', 5, 
 '某某大学', '用户主动取消', '2024-11-19 14:30:00', '2024-11-19 14:35:00');

-- 验证插入结果
SELECT 
    id, 
    order_no, 
    sku_name, 
    amount, 
    status,
    CASE status 
        WHEN 0 THEN '待支付'
        WHEN 1 THEN '支付完成'
        WHEN 2 THEN '出货成功'
        WHEN 3 THEN '出货失败'
        WHEN 4 THEN '已取消'
        ELSE '未知'
    END as status_desc,
    inner_code,
    addr,
    create_time,
    update_time
FROM tb_order 
WHERE id IN (1,2,3,4,5)
ORDER BY id;
