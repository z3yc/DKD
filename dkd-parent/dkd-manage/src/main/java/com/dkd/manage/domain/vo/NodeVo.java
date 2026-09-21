package com.dkd.manage.domain.vo;

import com.dkd.common.annotation.Excel;
import com.dkd.manage.domain.Node;
import com.dkd.manage.domain.Partner;
import com.dkd.manage.domain.Region;
import lombok.Data;

@Data
public class NodeVo extends Node {

    // 设备数量
    private Integer vmCount;

    // 区域
    private Region region;

    // 合作商
    private Partner partner;

    // 导出字段 - 区域名称
    @Excel(name = "所在区域")
    private String regionName;

    // 导出字段 - 合作商名称
    @Excel(name = "合作商")
    private String partnerName;
}
