package com.dkd.app.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.dkd.app.domain.TaskDetails;
import com.dkd.app.domain.VendingMachine;

import java.util.List;

public interface VendingMachineService extends IService<VendingMachine> {

    // 更改售货机状态
    boolean updateStatus(String innerCode, Integer vmStatus);

    /**
     * 补货完成回写（货道缓存 + 库存台账 + 流水，同一事务）。
     *
     * <p>为什么多一个 operator 参数：库存流水 tb_inventory_log 必须记 operator_name
     * （清晰版 §2.2 / §4.3 的"审计必写"要求），由工单执行人姓名传入。
     *
     * @param innerCode 设备编号
     * @param details 补货工单明细
     * @param operator 操作人姓名（工单执行人）
     * @return 是否成功
     */
    boolean supply(String innerCode, List<TaskDetails> details, String operator);
}
