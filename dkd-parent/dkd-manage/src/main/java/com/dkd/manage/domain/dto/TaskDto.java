package com.dkd.manage.domain.dto;

import lombok.Data;

import java.util.List;
@Data
public class TaskDto {
    private Long createType ;//创建类型
    private String innerCode ;//设备编号
    private Long userId ;//用户id
    private Long assignorId ;//创建人id
    private Long productTypeId;//工单类型
    private String desc;//工单描述
    private List<TaskDetailsDto> details ;//工单明细
}
