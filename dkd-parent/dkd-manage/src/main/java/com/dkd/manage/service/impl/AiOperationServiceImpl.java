package com.dkd.manage.service.impl;

import com.dkd.common.ai.enums.AiSuggestionType;
import com.dkd.common.ai.service.IAiService;
import com.dkd.manage.domain.VendingMachine;
import com.dkd.manage.service.IVendingMachineService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;

@Service
public class AiOperationServiceImpl {

    @Autowired
    private IVendingMachineService vendingMachineService;
    
    @Autowired
    private IAiService aiService;

    /**
     * 生成智能补货与运营建议
     * @param innerCode 设备编号
     * @return AI 的建议文本
     */
    public String getSmartAdvice(String innerCode) {
        // 1. 获取设备基础信息
        VendingMachine vm = vendingMachineService.selectVendingMachineByInnerCode(innerCode);
        if (vm == null) {
            return "未找到该设备信息";
        }

        // 2. 构建设备信息Map
        Map<String, Object> inputData = new HashMap<>();
        inputData.put("innerCode", vm.getInnerCode());
        inputData.put("addr", vm.getAddr());
        inputData.put("vmTypeId", vm.getVmTypeId());
        inputData.put("vmStatus", vm.getVmStatus() == 1L ? "运营中" : "未投放");
        inputData.put("channelMaxCapacity", vm.getChannelMaxCapacity());

        // 3. 调用AI服务生成运营建议
        return aiService.generateSuggestion("device", inputData, AiSuggestionType.OPERATION.getCode());
    }
}